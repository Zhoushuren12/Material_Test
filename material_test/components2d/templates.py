from __future__ import annotations

from pathlib import Path

from .model import ComponentProject


def elastic(tag, k, response="force_deformation"):
    return {"tag": tag, "name": f"Elastic {tag}", "response": response,
            "command": "uniaxialMaterial Elastic {tag} {k}", "parameters": [{"name": "k", "value": float(k)}]}


def node(tag, x, y, fix=(0, 0, 0), name=""):
    return {"tag": tag, "name": name, "x": float(x), "y": float(y), "fix": list(fix)}


def link(tag, i, j, materials, directions, name="", axis=(1., 0.)):
    return {"tag": tag, "name": name, "type": "zeroLength", "i": i, "j": j,
            "materials": list(materials), "directions": list(directions), "axis": list(axis)}


def element_channel(name, element, response, index=0, factor=1.):
    return {"name": name, "quantity": "element", "element": element,
            "response": response, "index": index, "factor": float(factor)}


def set_channel_units(project):
    force,length = project.data['units']['force'],project.data['units']['length']
    for channel in project.data['channels']:
        name = channel['name']
        if name in {'moment'}: channel['unit'] = f'{force}*{length}'
        elif name in {'theta','rotation'}: channel['unit'] = 'rad'
        elif name in {'velocity'}: channel['unit'] = f'{length}/s'
        elif 'force' in name or name in {'pin_x','pin_y'}: channel['unit'] = force
        else: channel['unit'] = length
    return project


def base_project(base_dir, solver, name):
    return {"schema": "material-test/component-2d/v1", "name": name,
            "units": {"force": "kN", "length": "mm", "time": "s"},
            "solver": {"executable_path": str(solver), "timeout": 120.},
            "output_dir": "component_outputs", "nodes": [], "materials": [], "elements": [],
            "constraints": [], "channels": [], "evidence": {"source_type": "manual-assumption"}}


def make_translation(base_dir, solver, *, direction=1, viscous=False):
    data = base_project(base_dir, solver, "Viscous damper" if viscous else "Translational damper")
    flags = [1, 1, 1]; flags[direction - 1] = 0
    data["nodes"] = [node(1, 0, 0, (1, 1, 1), "fixed port"), node(2, 0, 0, flags, "loaded port")]
    data["materials"] = [elastic(1, 100.)]
    if viscous:
        data["materials"] = [{"tag": 1, "name": "Viscous", "response": "force_deformation", "rate_dependent": True,
                              "command": "uniaxialMaterial Viscous {tag} {C} {alpha}",
                              "parameters": [{"name": "C", "value": 10.}, {"name": "alpha", "value": 1.}]}]
    data["elements"] = [link(1, 1, 2, [1], [direction], "damper")]
    data["loading"] = {"node": 2, "dof": direction, "path": [0, 1, -1, 0], "step": .05,
                       "analysis": "transient" if viscous else "static", "dt": .01 if viscous else 1.}
    data["channels"] = [{"name": "displacement", "quantity": "disp", "node": 2, "dof": direction},
                        element_channel("force", 1, "force", 3 + direction - 1),
                        {"name": "velocity", "quantity": "velocity", "node": 2, "dof": direction}]
    data["plot"] = {"x": "displacement", "y": "force"}
    data['ports'] = {'i':{'node':1,'dofs':[direction]},'j':{'node':2,'dofs':[direction]}}
    data['groups'] = [{'id':'damper','role':'component','nodes':[1,2],'elements':[1]}]
    return set_channel_units(ComponentProject(data, Path(base_dir))).validate()


def make_rotation(base_dir, solver):
    project = make_translation(base_dir, solver)
    d = project.data
    d["name"] = "Rotational damper"
    d["nodes"][1]["fix"] = [1, 1, 0]
    d["materials"][0]["response"] = "moment_rotation"
    d["elements"][0]["directions"] = [3]
    d["loading"].update(dof=3, path=[0, .01, -.01, 0], step=.0005)
    d["channels"] = [{"name": "rotation", "quantity": "disp", "node": 2, "dof": 3},
                     element_channel("moment", 1, "force", 5)]
    d["plot"] = {"x": "rotation", "y": "moment"}
    d['ports'] = {'i':{'node':1,'dofs':[3]},'j':{'node':2,'dofs':[3]}}
    return set_channel_units(project).validate()


def make_plate_joint(base_dir, solver, *, specimen=False, h_upper=150., h_lower=150., plate_k=50.):
    data = base_project(base_dir, solver, "Explicit upper/lower plate joint" + (" specimen" if specimen else ""))
    data["nodes"] = [node(101, 0, 0, (0, 0, 0) if specimen else (1, 1, 1), "column port"),
                     node(102, 0, 0, name="beam port"),
                     node(111, 0, h_upper, name="upper column"), node(112, 0, h_upper, name="upper beam"),
                     node(121, 0, -h_lower, name="lower column"), node(122, 0, -h_lower, name="lower beam")]
    data["materials"] = [elastic(11, plate_k), elastic(21, 10000.), elastic(22, 10000.)]
    data["elements"] = [link(201, 111, 112, [11], [1], "upper plate"),
                        link(202, 121, 122, [11], [1], "lower plate"),
                        link(203, 101, 102, [21, 22], [1, 2], "pin: no moment channel")]
    for element in data["elements"][:2]:
        element["display"] = {"shape": "plate", "length": 330., "width": 35.}
    data["elements"][2]["display"] = {"shape": "pin", "diameter": 65.}
    data["constraints"] = [{"type": "rigidLink", "retained": r, "constrained": s}
                           for r, s in ((101, 111), (101, 121), (102, 112), (102, 122))]
    data["channels"] = [element_channel("upper_force", 201, "force", 3),
                        element_channel("lower_force", 202, "force", 3),
                        element_channel("upper_deformation", 201, "deformation"),
                        element_channel("lower_deformation", 202, "deformation"),
                        element_channel("pin_x", 203, "force", 3), element_channel("pin_y", 203, "force", 4),
                        {"name": "theta", "terms": [{"quantity": "disp", "node": 102, "dof": 3},
                                                        {"quantity": "disp", "node": 101, "dof": 3, "factor": -1}]},
                        {"name": "moment", "terms": [{"quantity": "element", "element": 201, "response": "force", "index": 3, "factor": -h_upper},
                                                         {"quantity": "element", "element": 202, "response": "force", "index": 3, "factor": h_lower}]}]
    data["loading"] = {"node": 102, "dof": 3, "path": [0, .005, -.005, 0, .01, -.01, 0], "step": .0002, "analysis": "static", "dt": 1.}
    data["plot"] = {"x": "theta", "y": "moment"}
    data["geometry"] = {"h_upper": h_upper, "h_lower": h_lower, "plate_display_length": 330.,
                        "note": "Plate outline is display geometry, not zeroLength solver separation"}
    data['ports'] = {'column':{'node':101,'dofs':[1,2,3]},'beam':{'node':102,'dofs':[1,2,3]}}
    data['groups'] = [{'id':'joint','role':'component','nodes':[101,102,111,112,121,122],'elements':[201,202,203]}]
    if specimen:
        data["nodes"] += [node(103, 0, -750, (1, 1, 0), "column bottom"),
                          node(104, 0, 750, name="column actuator"),
                          node(105, 1115, 0, (0, 1, 0), "beam roller")]
        for eid, i, j, area, inertia in ((301, 103, 101, 8000., 1.3e8), (302, 101, 104, 8000., 1.3e8), (303, 102, 105, 4500., 7.5e7)):
            data["elements"].append({"tag": eid, "name": "column" if eid != 303 else "beam", "type": "elasticBeamColumn",
                                     "i": i, "j": j, "A": area, "E": 200., "Iz": inertia, "transformation": "Linear"})
        data["loading"].update(node=104, dof=1, path=[0, 3.75, -3.75, 0, 7.5, -7.5, 0], step=.5)
        data["channels"] += [{"name": "actuator_displacement", "quantity": "disp", "node": 104, "dof": 1},
                             {"name": "actuator_force", "quantity": "load_factor"}]
        data["plot"] = {"x": "actuator_displacement", "y": "actuator_force"}
        data["evidence"]["geometry_status"] = "assumed fixture; dimensions and supports require confirmation"
        data['groups'].append({'id':'specimen_fixture','role':'fixture','nodes':[103,104,105],'elements':[301,302,303]})
    return set_channel_units(ComponentProject(data, Path(base_dir))).validate()
