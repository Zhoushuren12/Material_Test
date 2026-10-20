from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import re

import numpy as np
import yaml

from material_test.domain import MaterialTemplate, SolverConfig
from material_test.modeling import defined_material_tags
from material_test.material_commands import parse_material_commands, validate_solver_materials


ELEMENT_TYPES = {"zeroLength", "twoNodeLink", "truss", "corotTruss", "elasticBeamColumn"}
RESPONSES = {"force_deformation", "stress_strain", "moment_rotation"}
ELEMENT_RESPONSES = {"force", "localForce", "axialForce", "deformation", "basicForce", "basicDeformation"}


def finite(value, name, *, positive=False):
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return number


def tag(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def ordered_history(loading):
    if "history" in loading:
        values = np.asarray(loading["history"], dtype=float)
    else:
        points = np.asarray(loading.get("path", [0, 1, -1, 0]), dtype=float)
        step = finite(loading.get("step", 0.1), "loading.step", positive=True)
        if points.ndim != 1 or len(points) < 2 or not np.isfinite(points).all():
            raise ValueError("loading.path needs at least two finite turning points")
        segments = [points[:1]]
        for start, end in zip(points[:-1], points[1:]):
            count = max(1, int(math.ceil(abs(end - start) / step)))
            if count > 1000000:
                raise ValueError("loading step is too small")
            segments.append(np.linspace(start, end, count + 1)[1:])
        values = np.concatenate(segments)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("loading.history needs at least two finite ordered samples")
    if values[0] != 0:
        raise ValueError("loading history must explicitly begin at zero")
    return values


@dataclass
class ComponentProject:
    data: dict
    base_dir: Path

    def clone(self):
        return ComponentProject(deepcopy(self.data), self.base_dir)

    @property
    def solver(self):
        return SolverConfig.from_mapping(self.data["solver"], base_dir=self.base_dir)

    @property
    def output_root(self):
        path = Path(self.data.get("output_dir", "component_outputs"))
        return (path if path.is_absolute() else self.base_dir / path).resolve()

    @property
    def materials(self):
        return {item["tag"]: MaterialTemplate.from_mapping(item) for item in self.data["materials"]}

    def validate(self):
        d = self.data
        if d.get("schema") != "material-test/component-2d/v1":
            raise ValueError("Unsupported planar component schema")
        units = d.get("units", {})
        if units.get("force") not in {"N", "kN", "MN"} or units.get("length") not in {"mm", "cm", "m"}:
            raise ValueError("Declare force and length units explicitly")
        if units.get("time", "s") != "s":
            raise ValueError("Time unit must be s; rotation unit is rad")
        nodes = {}
        for n in d.get("nodes", []):
            key = tag(n["tag"], "node tag")
            if key in nodes:
                raise ValueError(f"Duplicate node {key}")
            nodes[key] = n
            finite(n["x"], "node.x"); finite(n["y"], "node.y")
            for key2 in ("fix", "mass"):
                values = n.get(key2, [0, 0, 0])
                if len(values) != 3:
                    raise ValueError(f"node.{key2} must have three entries")
                if key2 == "fix" and any(v not in (0, 1) for v in values):
                    raise ValueError("node.fix entries must be 0 or 1")
                if key2 == "mass" and any(finite(v, "mass") < 0 for v in values):
                    raise ValueError("Nodal mass cannot be negative")
        if len(nodes) < 2:
            raise ValueError("At least two nodes required")
        materials, dimensions, all_material_tags = {}, {}, set()
        for item in d.get("materials", []):
            key = tag(item["tag"], "material tag")
            if key in materials:
                raise ValueError(f"Duplicate material {key}")
            material = MaterialTemplate.from_mapping(item)
            validate_solver_materials(material)
            commands = material.render_command()
            if any(line.strip() and not line.lstrip().startswith(("uniaxialMaterial ", "#"))
                   for line in commands.splitlines()):
                raise ValueError("Material resources accept only uniaxialMaterial commands, not model scripts")
            defined = defined_material_tags(commands)
            if key not in defined or all_material_tags & defined:
                raise ValueError("Undefined root or duplicate child material tag")
            all_material_tags.update(defined)
            dimension = item.get("response")
            if dimension not in RESPONSES:
                raise ValueError(f"Material {key}: declare response dimension")
            dimensions[key], materials[key] = dimension, material
        seen_materials = set()
        for material in materials.values():
            for command in parse_material_commands(material):
                if any(reference not in seen_materials for reference in command.referenced_tags):
                    raise ValueError('Wrapper materials must reference previously defined resources or child commands')
                seen_materials.add(command.tag)
        elements = {}
        for e in d.get("elements", []):
            key = tag(e["tag"], "element tag")
            if key in elements:
                raise ValueError(f"Duplicate element {key}")
            elements[key] = e
            if e["type"] not in ELEMENT_TYPES:
                raise ValueError(f"Unsupported element {e['type']}")
            if e["i"] not in nodes or e["j"] not in nodes or e["i"] == e["j"]:
                raise ValueError(f"Element {key}: invalid endpoints")
            i, j = nodes[e["i"]], nodes[e["j"]]
            length = math.hypot(j["x"] - i["x"], j["y"] - i["y"])
            if e["type"] == "zeroLength" and length > 1e-8:
                raise ValueError(f"zeroLength {key}: endpoints must coincide")
            if e["type"] in {"truss", "corotTruss", "elasticBeamColumn"} and length <= 1e-8:
                raise ValueError(f"Element {key}: physical member needs positive length")
            if e["type"] == "elasticBeamColumn":
                for prop in ("A", "E", "Iz"):
                    finite(e[prop], f"beam.{prop}", positive=True)
                if e.get("transformation", "Linear") not in {"Linear", "PDelta", "Corotational"}:
                    raise ValueError("Unsupported beam transformation")
                if e.get("release", 0) not in (0, 1, 2, 3):
                    raise ValueError("beam.release must be 0, 1, 2 or 3")
            elif e["type"] in {"truss", "corotTruss"}:
                finite(e["A"], "truss.A", positive=True)
                if dimensions.get(e.get("material")) != "stress_strain":
                    raise ValueError("Truss requires stress-strain material; area belongs to element")
            else:
                dirs, mats = e.get("directions", []), e.get("materials", [])
                if not dirs or len(dirs) != len(mats) or len(set(dirs)) != len(dirs):
                    raise ValueError("Link needs one material per unique direction")
                for direction, mat in zip(dirs, mats):
                    expected = "moment_rotation" if direction == 3 else "force_deformation"
                    if direction not in (1, 2, 3) or dimensions.get(mat) != expected:
                        raise ValueError(f"Element {key}: direction {direction} material dimension mismatch")
                axis = e.get("axis", [1., 0.])
                if len(axis) != 2 or math.hypot(*[finite(v, "axis") for v in axis]) <= 0:
                    raise ValueError("Link local axis must be a nonzero planar vector")
        slaves, retained = set(), set()
        for c in d.get("constraints", []):
            if c.get("type") not in {"rigidLink", "equalDOF"}:
                raise ValueError("Only rigidLink beam and equalDOF supported")
            r, s = c["retained"], c["constrained"]
            if r not in nodes or s not in nodes or r == s or s in slaves:
                raise ValueError("Invalid or multiply constrained MPC node")
            if any(nodes[s].get("fix", [0, 0, 0])):
                raise ValueError("Do not ground an MPC constrained node")
            if c["type"] == "equalDOF" and (not c.get("dofs") or any(v not in (1, 2, 3) for v in c["dofs"])):
                raise ValueError("equalDOF needs active DOFs 1, 2 or 3")
            slaves.add(s); retained.add(r)
        if slaves & retained:
            raise ValueError("Transformation constraint chains are not supported; use independent masters")
        for name,port in d.get('ports',{}).items():
            if not name or port.get('node') not in nodes or not port.get('dofs') or any(v not in (1,2,3) for v in port['dofs']):
                raise ValueError('Named component ports must reference existing nodes and active DOFs')
        group_ids = set()
        for group in d.get('groups',[]):
            if not group.get('id') or group['id'] in group_ids or group.get('role') not in {'component','fixture'}:
                raise ValueError('Groups need a unique id and component/fixture role')
            if any(n not in nodes for n in group.get('nodes',[])) or any(e not in elements for e in group.get('elements',[])):
                raise ValueError('Group references an undefined node or element')
            group_ids.add(group['id'])
        loading = d["loading"]
        control, dof = loading["node"], loading["dof"]
        if control not in nodes or dof not in (1, 2, 3) or control in slaves:
            raise ValueError("Loading must control an independent node DOF")
        if nodes[control].get("fix", [0, 0, 0])[dof - 1]:
            raise ValueError("Controlled DOF cannot also be fixed")
        ordered_history(loading)
        analysis = loading.get("analysis", "static")
        if analysis not in {"static", "transient"}:
            raise ValueError("analysis must be static or transient")
        finite(loading.get("dt", 1.), "loading.dt", positive=True)
        finite(loading.get("equilibrium_tolerance", 1e-6), "loading.equilibrium_tolerance", positive=True)
        if analysis == "static":
            for item in d["materials"]:
                command = materials[item["tag"]].render_command()
                if item.get("rate_dependent") or re.search(r"\b(Viscous|ViscousDamper)\b", command):
                    raise ValueError("Rate-dependent materials need a transient protocol with physical time")
        names = set()
        for channel in d.get("channels", []):
            name = channel["name"]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name in names or name in {"input","time","sample","load_multiplier"} or name.startswith("node_"):
                raise ValueError("Unique ASCII response channel names required")
            names.add(name)
            terms = channel.get("terms", [channel])
            if not terms:
                raise ValueError("Response channel needs at least one source")
            for term in terms:
                finite(term.get("factor", 1.), "channel.factor")
                quantity = term["quantity"]
                if quantity in {"disp", "reaction", "velocity", "acceleration"}:
                    if term.get("node") not in nodes or term.get("dof") not in (1, 2, 3):
                        raise ValueError("Invalid node response source")
                elif quantity == "element":
                    if term.get("element") not in elements or term.get("response") not in ELEMENT_RESPONSES:
                        raise ValueError("Invalid element response source")
                    if not isinstance(term.get("index", 0), int) or term.get("index", 0) < 0:
                        raise ValueError("Element response index is zero-based and nonnegative")
                elif quantity == "load_factor":
                    if analysis != "static":
                        raise ValueError("Load multiplier response applies to static reference-load tests")
                else:
                    raise ValueError(f"Unknown channel quantity {quantity}")
        if not names:
            raise ValueError("Define at least one response channel")
        self.solver
        return self


def load_component(path):
    path = Path(path).resolve()
    project = ComponentProject(yaml.safe_load(path.read_text(encoding="utf-8")), path.parent)
    return project.validate()


def save_component(project, path):
    project.validate()
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = deepcopy(project.data)
    if path.parent != project.base_dir.resolve():
        data['solver'] = project.solver.to_mapping()
        data['output_dir'] = str(project.output_root)
        for observation in data.get('fitting',{}).get('observations',[]):
            source = Path(observation['path'])
            if not source.is_absolute(): observation['path'] = str((project.base_dir/source).resolve())
        if data.get('last_result') and not Path(data['last_result']).is_absolute():
            data['last_result'] = str((project.base_dir/data['last_result']).resolve())
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
