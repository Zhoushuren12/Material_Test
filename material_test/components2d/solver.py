from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import numpy as np

from material_test.cancellation import AnalysisCancelled
from material_test.opensees.runner import _resolve_tcl_library
from .model import ComponentProject, ordered_history, save_component


@dataclass
class ComponentResult:
    run_dir: Path
    columns: tuple[str, ...]
    values: np.ndarray
    model_file: Path
    stdout: str
    stderr: str

    def channel(self, name):
        return self.values[:, self.columns.index(name)]


def input_signature(project):
    keys = ('units','nodes','elements','constraints','loading','channels')
    data = {key:project.data.get(key) for key in keys}
    data['materials'] = {str(tag):material.render_command() for tag,material in project.materials.items()}
    return hashlib.sha256(json.dumps(data,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()


def load_component_result(project, directory):
    from .model import load_component
    directory = Path(directory).resolve()
    if not directory.is_relative_to(project.output_root):
        raise ValueError('Stored result lies outside the project output directory')
    metadata = json.loads((directory/'result.json').read_text(encoding='utf-8'))
    if metadata.get('status') != 'complete':
        raise ValueError('Stored result is incomplete')
    expected = metadata.get('input_sha256') or input_signature(load_component(directory/'project.yaml'))
    if input_signature(project) != expected:
        raise ValueError('Stored result is stale; model inputs have changed')
    for name,key in (('model.tcl','model_sha256'),('response.csv','response_sha256')):
        if key in metadata and hashlib.sha256((directory/name).read_bytes()).hexdigest() != metadata[key]:
            raise ValueError(f'Stored {name} checksum mismatch')
    if metadata.get('solver_sha256') != hashlib.sha256(project.solver.executable_path.read_bytes()).hexdigest():
        raise ValueError('Stored result was calculated by a different solver binary')
    values = np.loadtxt(directory/'response.csv',delimiter=',',skiprows=1,ndmin=2)
    columns = tuple(metadata['columns'])
    if values.shape != (metadata['samples'],len(columns)) or not np.isfinite(values).all():
        raise ValueError('Stored response shape or values are invalid')
    np.testing.assert_allclose(values[:,columns.index('input')],ordered_history(project.data['loading']),rtol=1e-8,atol=1e-8)
    return ComponentResult(directory,columns,values,directory/'model.tcl',
                           (directory/'stdout.log').read_text(encoding='utf-8'),(directory/'stderr.log').read_text(encoding='utf-8'))


def truss_geometry(element,nodes):
    i,j = nodes[element['i']],nodes[element['j']]
    dx,dy = float(j['x']-i['x']),float(j['y']-i['y'])
    length = float(np.hypot(dx,dy))
    return dx,dy,length


def response_expression(term, project=None):
    quantity = term["quantity"]
    if quantity == "element":
        value = f"[lindex [eleResponse {term['element']} {term['response']}] {term.get('index', 0)}]"
        element = next(e for e in project.data['elements'] if e['tag'] == term['element']) if project else None
        if element and element['type'] in {'truss','corotTruss'} and term['response'] != 'force':
            nodes = {n['tag']:n for n in project.data['nodes']}
            dx,dy,length = truss_geometry(element,nodes)
            i,j = element['i'],element['j']
            ux,uy = f'([nodeDisp {j} 1]-[nodeDisp {i} 1])',f'([nodeDisp {j} 2]-[nodeDisp {i} 2])'
            tx,ty = f'({dx:.17g}+{ux})',f'({dy:.17g}+{uy})'
            current_length = f'sqrt({tx}*{tx}+{ty}*{ty})'
            cx,cy = (f'({tx}/{current_length})',f'({ty}/{current_length})') if element['type'] == 'corotTruss' else (f'{dx/length:.17g}',f'{dy/length:.17g}')
            axial = f'([lindex [eleResponse {element["tag"]} force] 3]*{cx}+[lindex [eleResponse {element["tag"]} force] 4]*{cy})'
            if term['response'] in {'axialForce','basicForce'}:
                value = axial
            elif term['response'] in {'deformation','basicDeformation'}:
                value = f'({current_length}-{length:.17g})' if element['type'] == 'corotTruss' else f'({ux}*{cx}+{uy}*{cy})'
            elif term['response'] == 'localForce':
                index=term.get('index',0)
                value = f'-{axial}' if index==0 else axial if index==3 else '0.'
    elif quantity == "load_factor":
        value = "[getLoadFactor 1]"
    else:
        command = {"disp": "nodeDisp", "reaction": "nodeReaction", "velocity": "nodeVel", "acceleration": "nodeAccel"}[quantity]
        value = f"[{command} {term['node']} {term['dof']}]"
    return f"({float(term.get('factor', 1.)):.16g} * {value})"


def write_component_tcl(project, work_dir, *, compact=False, record_displacements=False):
    project.validate()
    d, work_dir = project.data, Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    history = ordered_history(d["loading"])
    np.savetxt(work_dir / "history.txt", history, fmt="%.16g")
    lines = ["wipe", "set tcl_precision 17", "model BasicBuilder -ndm 2 -ndf 3"]
    for material in project.materials.values():
        lines.append(material.render_command())
    for node in d["nodes"]:
        n = node["tag"]
        lines.append(f"node {n} {float(node['x']):.16g} {float(node['y']):.16g}")
        lines.append(f"fix {n} " + " ".join(str(v) for v in node.get("fix", [0, 0, 0])))
        if any(node.get("mass", [])):
            lines.append(f"mass {n} " + " ".join(f"{float(v):.16g}" for v in node["mass"]))
    for e in d["elements"]:
        head = f"element {e['type']} {e['tag']} {e['i']} {e['j']}"
        if e["type"] == "elasticBeamColumn":
            transform = e["tag"]
            lines.append(f"geomTransf {e.get('transformation', 'Linear')} {transform}")
            args = f" {e['A']:.16g} {e['E']:.16g} {e['Iz']:.16g} {transform}"
            if e.get("release"):
                args += f" -release {e['release']}"
        elif e["type"] in {"truss", "corotTruss"}:
            args = f" {e['A']:.16g} {e['material']}"
        else:
            x, y = e.get("axis", [1., 0.])
            args = " -mat " + " ".join(str(v) for v in e["materials"])
            args += " -dir " + " ".join(str(v) for v in e["directions"])
            args += f" -orient {x:.16g} {y:.16g} 0 {-y:.16g} {x:.16g} 0"
        lines.append(head + args)
    for c in d.get("constraints", []):
        if c["type"] == "rigidLink":
            lines.append(f"rigidLink beam {c['retained']} {c['constrained']}")
        else:
            lines.append(f"equalDOF {c['retained']} {c['constrained']} " + " ".join(str(v) for v in c["dofs"]))
    types = {e['tag']:e['type'] for e in d['elements']}
    element_sources = sorted({(t['element'],'force' if types[t['element']] in {'truss','corotTruss'} else t['response'])
                              for c in d['channels'] for t in c.get('terms',[c]) if t['quantity'] == 'element'})
    for element, response in element_sources:
        lines.append(f'recorder Element -file "element_{element}_{response}.txt" -precision 17 -ele {element} {response}')
    lines += ['set record_index 0', 'set sample_index [open "sample_rows.txt" w]']
    loading = d["loading"]
    transient = loading.get("analysis", "static") == "transient"
    if not transient:
        # Linear reference loading makes recorder time the exact load factor.
        lines.append(f'recorder Node -file "load_state.txt" -time -precision 17 -node {loading["node"]} -dof {loading["dof"]} disp')
    dt = float(loading.get("dt", 1.)) if transient else 1.
    slaves = {c["constrained"] for c in d.get("constraints", [])}
    free_dofs = sum(sum(1 - v for v in n.get("fix", [0, 0, 0])) for n in d["nodes"] if n["tag"] not in slaves) - 1
    # Fully prescribed tests need multiplier equations, not fictitious free DOFs.
    handler = "Penalty 1.0e12 1.0e12" if transient and (free_dofs == 0 or loading['node'] in {c['retained'] for c in d.get('constraints',[])}) else "Transformation"
    # DisplacementControl can have zero displacement corrections with a large
    # force residual at the controlled DOF, especially when MinMax branches
    # fail. Check equilibrium for static tests before accepting a load factor.
    convergence = "NormDispIncr" if transient else "NormUnbalance"
    tolerance = "1.0e-9" if transient else f"{float(loading.get('equilibrium_tolerance', 1e-6)):.16g}"
    if transient:
        lines += [f'timeSeries Path 1 -dt {dt:.16g} -filePath "history.txt" -useLast',
                  f"pattern Plain 1 1 {{ sp {loading['node']} {loading['dof']} 1.0 }}"]
    else:
        reference = [0.,0.,0.]; reference[loading['dof']-1] = 1.
        lines += ["timeSeries Linear 1", f"pattern Plain 1 1 {{ load {loading['node']} " + " ".join(str(v) for v in reference) + " }",
                  'set h [open "history.txt" r]', 'set targets [split [string trim [read $h]] "\\n"]', 'close $h', 'set sample 0']
    lines += [f"constraints {handler}", "numberer RCM", "system BandGeneral",
              f"test {convergence} {tolerance} 80", "algorithm Newton"]
    lines += ["integrator Newmark 0.5 0.25", "analysis Transient"] if transient else [f"integrator DisplacementControl {loading['node']} {loading['dof']} 1.0e-6", "analysis Static"]
    # Reduce increments against the same Path series, preserving original sample times.
    lines += ["proc advance {delta depth} {", "    global record_index", "    algorithm Newton"]
    lines += ["    set ok [analyze 1 $delta]"] if transient else [f"    integrator DisplacementControl {loading['node']} {loading['dof']} $delta", "    set ok [analyze 1]"]
    lines += ["    if {$ok == 0} { incr record_index; return 0 }", "    if {$depth >= 8} { return $ok }",
              "    set half [expr {$delta / 2.0}]", "    set next [expr {$depth + 1}]",
              "    set ok [advance $half $next]", "    if {$ok != 0} { return $ok }",
              "    return [advance $half $next]", "}"]
    node_sources = [(n['tag'],key,dof) for n in d['nodes'] for key in ('u','r','v','a') for dof in (1,2,3)]
    if compact:
        required = {(loading['node'],'u',loading['dof'])}
        if record_displacements:
            required.update((n['tag'], 'u', dof) for n in d['nodes'] for dof in (1, 2, 3))
        for channel in d['channels']:
            for term in channel.get('terms',[channel]):
                if term['quantity'] in {'disp','reaction','velocity','acceleration'}:
                    required.add((term['node'],{'disp':'u','reaction':'r','velocity':'v','acceleration':'a'}[term['quantity']],term['dof']))
        for element in d['elements']:
            if element['type'] in {'truss','corotTruss'}:
                required.update((node,'u',dof) for node in (element['i'],element['j']) for dof in (1,2))
        node_sources = sorted(required)
    node_columns = [f"node_{n}_{key}{dof}" for n,key,dof in node_sources]
    columns = ("time" if transient else "sample", "input", "load_multiplier", *(c["name"] for c in d["channels"]), *node_columns)
    expressions = ["[getTime]" if transient else "$sample", f"[nodeDisp {loading['node']} {loading['dof']}]", "[getLoadFactor 1]"]
    for channel in d["channels"]:
        expressions.append("[expr {" + " + ".join(response_expression(t,project) for t in channel.get("terms", [channel])) + "}]")
    for n,key,dof in node_sources:
        cmd = {'u':'nodeDisp','r':'nodeReaction','v':'nodeVel','a':'nodeAccel'}[key]
        expressions.append(f'[{cmd} {n} {dof}]')
    lines += ['set out [open "response.csv" w]', f'puts $out "{",".join(columns)}"',
              "proc recordState {out} {", "    global sample record_index sample_index", "    reactions" + (" -dynamic" if transient else ""),
              '    puts $out "' + ",".join(expressions) + '"', "    flush $out", "}",
              "record", "recordState $out", 'puts $sample_index $record_index', f"for {{set i 1}} {{$i < {len(history)}}} {{incr i}} {{"]
    if transient:
        lines += [f"    set ok [advance {dt:.16g} 0]"]
    else:
        lines += ["    set sample $i", f"    set increment [expr {{[lindex $targets $i] - [nodeDisp {loading['node']} {loading['dof']}]}}]",
                  "    set ok 0", "    if {abs($increment) > 1.0e-15} { set ok [advance $increment 0] }"]
    lines += ["    if {$ok != 0} {", "        close $out",
              '        puts stderr "COMPONENT_FAILED_AT_SAMPLE $i"', "        exit 3", "    }",
              "    recordState $out", '    puts $sample_index $record_index', "}", "close $out", "close $sample_index", 'puts "COMPONENT_COMPLETE"', "wipe", "exit 0"]
    path = work_dir / "model.tcl"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path, columns, history


def run_component(project: ComponentProject, *, run_dir=None, cancel_check=None, scratch=False, record_displacements=False):
    project.validate()
    if cancel_check and cancel_check():
        raise AnalysisCancelled("Component calculation cancelled")
    solver = project.solver
    executable = solver.executable_path.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    root = project.output_root
    directory = Path(run_dir).resolve() if run_dir else root / (datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    if not directory.is_relative_to(root):
        raise ValueError("All component outputs must stay inside the configured project output directory")
    if not scratch and (directory / "response.csv").exists():
        raise FileExistsError("Choose a fresh run directory; existing results are never overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    model, columns, history = write_component_tcl(project, directory, compact=scratch,
                                                 record_displacements=record_displacements)
    save_component(project, directory / "project.yaml")
    env = os.environ.copy()
    library = _resolve_tcl_library(solver, executable)
    if library:
        env["TCL_LIBRARY"] = str(library)
    # Relative source via stdin avoids Unicode argv failures in older Windows Tcl builds.
    process = subprocess.Popen([str(executable)], cwd=directory, env=env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    payload = 'source "model.tcl"\nexit\n'
    try:
        while True:
            if cancel_check and cancel_check():
                raise AnalysisCancelled("Component calculation cancelled")
            if time.monotonic() - started > solver.timeout:
                raise TimeoutError("OpenSees component timeout")
            try:
                stdout, stderr = process.communicate(payload, timeout=.1)
                break
            except subprocess.TimeoutExpired:
                payload = None
    except BaseException:
        process.kill(); process.communicate()
        raise
    (directory / "stdout.log").write_text(stdout, encoding="utf-8")
    (directory / "stderr.log").write_text(stderr, encoding="utf-8")
    if process.returncode or "COMPONENT_COMPLETE" not in stdout + stderr:
        raise RuntimeError(f"OpenSees component failed; diagnostics: {directory}\n{stdout}\n{stderr}")
    values = np.loadtxt(directory / "response.csv", delimiter=",", skiprows=1, ndmin=2)
    if values.shape != (len(history), len(columns)) or not np.isfinite(values).all():
        raise RuntimeError("Incomplete or non-finite component response")
    # Older Tcl eleResponse formatting truncates doubles; use precision-17
    # recorders and original-sample commit indices, including adaptive substeps.
    sample_rows = np.loadtxt(directory / "sample_rows.txt",dtype=int,ndmin=1)
    if len(sample_rows) != len(history):
        raise RuntimeError("Missing original-sample recorder indices")
    raw_elements = {}
    elements = {e['tag']:e for e in project.data['elements']}
    for channel in project.data['channels']:
        for term in channel.get('terms',[channel]):
            if term['quantity'] == 'element':
                element = elements[term['element']]
                key = (term['element'],'force' if element['type'] in {'truss','corotTruss'} else term['response'])
                if key not in raw_elements:
                    raw = np.loadtxt(directory / f"element_{key[0]}_{key[1]}.txt",ndmin=2)
                    if max(sample_rows) >= len(raw):
                        raise RuntimeError("Incomplete precision element recorder")
                    raw_elements[key] = raw[sample_rows]
    positions = {name:i for i,name in enumerate(columns)}
    if project.data['loading'].get('analysis', 'static') == 'static':
        load_state = np.loadtxt(directory / 'load_state.txt', ndmin=2)
        if max(sample_rows) >= len(load_state):
            raise RuntimeError('Incomplete precision reference-load recorder')
        values[:, positions['load_multiplier']] = load_state[sample_rows, 0]
    for channel in project.data['channels']:
        response = np.zeros(len(history))
        for term in channel.get('terms',[channel]):
            quantity = term['quantity']
            if quantity == 'element':
                element = elements[term['element']]
                is_truss = element['type'] in {'truss','corotTruss'}
                raw = raw_elements[(term['element'],'force' if is_truss else term['response'])]
                if is_truss and term['response'] != 'force':
                    nodes = {n['tag']:n for n in project.data['nodes']}
                    dx,dy,length = truss_geometry(element,nodes)
                    ux = values[:,positions[f"node_{element['j']}_u1"]]-values[:,positions[f"node_{element['i']}_u1"]]
                    uy = values[:,positions[f"node_{element['j']}_u2"]]-values[:,positions[f"node_{element['i']}_u2"]]
                    current_length = np.hypot(dx+ux,dy+uy)
                    cx,cy = ((dx+ux)/current_length,(dy+uy)/current_length) if element['type'] == 'corotTruss' else (dx/length,dy/length)
                    axial = raw[:,3]*cx+raw[:,4]*cy
                    if term['response'] in {'deformation','basicDeformation'}:
                        raw = (current_length-length if element['type'] == 'corotTruss' else ux*cx+uy*cy)[:,None]
                    elif term['response'] in {'axialForce','basicForce'}:
                        raw = axial[:,None]
                    elif term['response'] == 'localForce':
                        raw = np.zeros((len(history),6)); raw[:,0],raw[:,3] = -axial,axial
                index = term.get('index',0)
                if index >= raw.shape[1]:
                    raise RuntimeError("Requested element response component is unavailable")
                source = raw[:,index]
            elif quantity == 'load_factor':
                source = values[:,positions['load_multiplier']]
            else:
                prefix = {'disp':'u','reaction':'r','velocity':'v','acceleration':'a'}[quantity]
                source = values[:,positions[f"node_{term['node']}_{prefix}{term['dof']}"]]
            response += term.get('factor',1.)*source
        values[:,positions[channel['name']]] = response
    np.savetxt(directory / 'response.csv',values,delimiter=',',comments='',header=','.join(columns),fmt='%.17g')
    if not np.allclose(values[:, 1], history, atol=1e-8, rtol=1e-8):
        raise RuntimeError("Component response does not preserve prescribed loading history")
    dt = float(project.data["loading"].get("dt", 1.)) if project.data["loading"].get("analysis") == "transient" else 1.
    if not np.allclose(values[:, 0], np.arange(len(history)) * dt, atol=1e-7):
        raise RuntimeError("Response sample times do not match prescribed history")
    metadata = {"schema": "material-test/component-result/v1", "samples": len(history), "columns": columns,
                "solver": str(executable), "solver_sha256": hashlib.sha256(executable.read_bytes()).hexdigest() if not scratch else None,
                "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                "input_sha256": input_signature(project),
                "response_sha256": hashlib.sha256((directory/'response.csv').read_bytes()).hexdigest(), "status": "complete",
                "evidence": project.data.get("evidence", {"source_type": "manual-assumption"})}
    (directory / "result.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return ComponentResult(directory, tuple(columns), values, model, stdout, stderr)
