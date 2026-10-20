# Planar Solver And CLI

The planar solver is the backend of the main **支撑装配** canvas. There is no
separate header model mode. Normal projects save explicit graphs under
`modeling.assembly`, sharing materials, loading and calibration with the existing
workflow. The main window can import component YAML geometry/materials/history
into this same editor; the imported project must be saved as a normal project.
Original ports/groups/channel metadata is retained as source metadata, not
exposed as extra UI workflows. Projects with dedicated observation fitting are
rejected on GUI import rather than silently discarding the fit contract.

The independent component schema remains available for advanced CLI analysis,
multi-channel observations and image fitting. `component-gui` imports supported
models into the normal material-test main window:

```powershell
python -m material_test.cli component-gui path/to/component.yaml
python -m material_test.cli component-run path/to/component.yaml
python -m material_test.cli component-run path/to/component.yaml --fit
```

## Model Contract

`material-test/component-2d/v1` stores real planar node coordinates, UX/UY/RZ,
supports, consistent nodal mass, material resources, member properties, local
axes, multipoint constraints, loading and named response channels. A component
and its test fixture are explicit entities in the same graph; the schema is not
tied to any named device. The plate-joint template is one example, not a special
solver branch.

Supported initial elements: zeroLength, twoNodeLink, truss, corotTruss and
elasticBeamColumn with Linear/PDelta/Corotational transformations. Material
commands use the existing MaterialTemplate contract and named parameters.
Axial members require stress-strain resources; link translations require
force-deformation laws; link rotations require moment-rotation laws. No hidden
area/length conversion is applied to generalized spring laws.

RigidLink beam and equalDOF constraints are supported. Duplicate slave-node
definitions and Transformation master/slave chains are rejected. The rigid-arm
assumption is small rotation; choosing corotTruss alone does not remove that
limitation. Zero-length endpoints must coincide. Display-only plate and pin
outlines never alter solver node coordinates. Coincident nodes retain separate
tags, selections, DOFs and material histories.

Templates cover axial, shear, rotational and viscous devices, a local explicit
upper/lower-plate joint, and a full beam-column specimen. The main GUI supports
mouse-based nodes/elements, supports, member properties, material assignment
and control-node/direction selection. Advanced mass and multipoint-constraint
data can be declared in YAML; the compact GUI does not provide their full editor.

## Loading and Results

Static testing uses incremental DisplacementControl with a unit reference
force/moment. Static `sample` is a protocol index, not physical time; the
`load_multiplier` gives the applied unit-reference load. A loaded node's
reaction is not its actuator force under reference-load control. Transient
testing uses explicit physical dt and Newmark integration. Rate-dependent
materials are rejected by the static workflow. Fully prescribed transient
tests use penalty constraints, checked against the requested displacement.

Adaptive subdivision retains original sample positions. Missing, non-finite
and incomplete histories are rejected. Precision-17 element recorders and
original-sample commit indices avoid older Tcl eleResponse rounding. All
nodal displacement, reaction, velocity and acceleration components are saved.
The static reference-load multiplier is recovered from a precision-17 recorder
time column (the pattern is Linear), avoiding older getLoadFactor formatting.
Truss axial force is projected from its recorded global force and deformation
is derived from node motion; corotTruss uses the current axis and length. This
also works when an older solver does not implement a requested basic alias.
Named channels may combine node quantities, element response components and
the static reference-load multiplier. Element response indices are zero-based;
force signs follow OpenSees element order and declared local axes.

Each production run has a fresh output directory containing the YAML snapshot,
Tcl model, loading, raw precision recorders, response CSV, logs and checksum
manifest. Existing production results are not overwritten. Fitting reuses one
explicit scratch directory, records every candidate's parameters/objective in
evaluations.csv, preserves best_so_far.yaml, and reruns the selected best model
in a fresh verified_best directory. Cancellation never applies candidate
parameters to the source project. GUI fitting requires explicit parameter
application.

## Observations and Fitting

Material parameters may be tied across multiple resources; member A/E/Iz may
also be selected explicitly. Each observation specifies response channels and
a positive weight. Ordered measured histories must match the complete loading
sequence; there is no interpolation across a hysteresis abscissa.

Named ports and component/fixture groups keep installation interfaces separate
from test-fixture bookkeeping. Saved references are resolved when a project is
relocated. Signed observation x_scale/y_scale conversions are explicit; image
unit mismatches without declared conversion are rejected. Parameter bindings
can use factor/offset maps, and local-only refinement can be selected after a
global search. Saved complete results are restored only after model, solver and
response checksum checks; changed input requires recalculation.

Image observations are **unordered** digitized spatial data. Axis calibration,
legend exclusions, printed-line thickness and source SHA-256 are retained.
The objective is bidirectional curve-to-image pixel distance. Coverage and
precision are reported at an explicit pixel tolerance. No sequence RMSE,
experimental per-cycle energy, timestamps or unique plate-material parameters
are claimed from an image point cloud. A good graphical match does not certify
the assumed fixture geometry or loading history.

This first extension is not a general shell/contact FE preprocessor, nor a
framework seismic-analysis interface. Friction/contact, instability, fatigue,
activation and self-centering behavior require appropriate executable material
laws and independent validation rather than assumptions based on device names.
