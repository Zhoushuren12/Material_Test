# Material Test

Python-first OpenSees material testing and material-parameter fitting tool.

The first version supports a practical manual-fitting loop:

1. Select an OpenSees executable.
2. Load an explicit material command template.
3. Edit material parameter values.
4. Define cyclic displacement loading.
5. Import experiment displacement-force data.
6. Run OpenSees through Python.
7. Overlay experiment and simulation curves for manual judgment.

The program supports automatic fitting with differential evolution (DE) or particle swarm optimization (PSO), followed by optional robust least-squares refinement. Batch studies and automatic custom-material discovery remain out of scope.

## Setup

```powershell
python -m pip install -e .
```

For tests:

```powershell
python -m pip install -e ".[dev]"
```

`PySide6` is required for the desktop UI. The non-UI core and CLI can run without launching the UI.

## CLI Smoke Test

```powershell
python -m material_test.cli run examples\projects\twostage_demo.yaml
```

This writes generated Tcl, response data, a simulation CSV, and `overlay.png` under `outputs\runs\...`.

The retained `Figure_1.png` legacy case from `Material_Test - 2` can be run with:

```powershell
python -m material_test.cli run examples\projects\figure_1_selfcentering.yaml
```

## Automatic Parameter Fitting

The `fit` command always uses the experimental displacement history as the OpenSees input path. This preserves loading and unloading order, which is essential for fitting hysteretic material response.

```powershell
python -m material_test.cli fit examples\projects\twostage_fit.yaml
```

The fitting section explicitly selects parameters and finite physical bounds:

```yaml
fitting:
  algorithm: de       # de (default) or pso
  max_evaluations: 240
  population_size: 16
  local_refine: true
  local_max_evaluations: 120
  loss: soft_l1
  seed: 42
  parameters:
    - name: k1
      min: 1000.0
      max: 20000.0
      scale: log       # optional: linear (default) or log
    - name: sigAct
      min: 1000.0
      max: 50000.0
```

`DE + local refinement` is the default high-accuracy workflow. Use `PSO + local refinement` when reproducing PSO-based studies or when its search behavior is preferred. Each fit writes `fitted_material.yaml`, `fit_summary.json`, `simulation_curve.csv`, and `fit_overlay.png` to its output directory.

The bundled `twostage_fit.yaml` is an executable workflow example. Its small sample dataset is not intended as a high-fidelity calibration dataset; use a complete displacement-force sequence recorded in loading order for engineering parameter identification.

In the desktop UI, choose the global algorithm on the **Parameter Fitting** page,
select the parameters to fit, and provide both Min and Max values. The normal
**Run** action remains available for checking a single parameter set.

The fifth workflow page, **Calculation & Comparison**, is the manual calibration
workspace. It edits the selected material parameters without returning to the
material-library page, runs the current model, and overlays the experiment,
baseline, and current responses. The current result can be frozen as a baseline
before another parameter change. Peak force, initial stiffness, hysteretic
integral, endpoint force, RMSE, and normalized RMSE are shown together; error
metrics are reported only when the calculation and experiment use the same
displacement sequence. The calculation can use either the configured loading
protocol or the imported experimental displacement history. The former standalone
results page has been removed, and its export and run log now live on this page.
Material-value editors accept decimal or scientific notation such as `1e6` and
`-2.5e-3`. The on-screen comparison uses an interactive PyQtGraph canvas with
direct wheel zoom, mouse-drag panning, context-menu controls, and auto-range
reset. **Export Current Comparison** writes PNG, PDF, or SVG with the publication
Matplotlib style: Times New Roman/SimSun, major inward ticks, explicit tick-bound
limits, 25-point axis labels, no title, and 300-dpi raster output.

## Material Resources and Brace Assembly

The desktop UI is organized as a six-step workflow. **Material Library &
Constitutive Models** manages reusable material definitions, parameters, and
single-material hysteresis responses. **Brace Assembly** builds a recursive
two-terminal network from material elements, series groups, and parallel groups.
Nodes and Truss element numbers are generated automatically, and the compact
topology preview updates while the tree is edited.

Each assembly element selects a tag from the defined material resources. A new
project starts with an empty material set, and elements may remain unassigned
while the assembly is being drafted. Empty or incomplete drafts can be saved and
reloaded; all elements must reference defined materials only when calculating,
running, or fitting. Deleting a used material preserves the assembly elements and
offers replacement or unassignment.
An element's area can override the project default. `length_weight` divides the
available length among children of the same series group; parallel children span
the same two terminal nodes.

Nested topologies are stored in project YAML:

```yaml
modeling:
  topology: nested
  root:
    type: series
    children:
      - type: parallel
        children:
          - {type: material, material_tag: 101}
          - {type: material, material_tag: 102}
      - type: material
        material_tag: 103
        area: 2.0
        length_weight: 1.5
```

Legacy `single`, `series`, and `parallel` project configurations remain supported.
Run `examples/projects/nested_topology_demo.yaml` for an executable mixed-topology
example.

## Built-in Material Library

The desktop **Material Library & Constitutive Models** page includes a local, versioned starter
library focused on SMA braces and steel-structure response. It currently covers
SMA and self-centering behavior, Steel01/Steel02, simplified gap/slip and
pinching models, Concrete02, and two viscous/viscoelastic models. Each entry
includes a link to the relevant OpenSees documentation or source code.

Use **Replace Material Set** to start a new material resource set, or **Add to
Material Resources** to append a uniquely tagged material. Assign resources to
elements later on the **Brace Assembly** page. Added parameters are automatically
namespaced, and the combined command is stored inline in the project YAML, so no
separate material-template file is required.

Each defined `uniaxialMaterial` command appears as a selectable row on the
material page. Clicking a row, or stepping through the loaded values in the
material Tag control, opens that material's current parameter description.
Material-library defaults are inserted automatically; this page keeps the values
read-only so material selection and constitutive documentation remain separate
from calibration. Parameter editing is centralized on **Calculation & Comparison**.
Metadata includes the Chinese meaning, unit, and the part of the hysteresis
response it primarily controls.

Library rows include compact hysteresis previews generated by the configured
OpenSees executable. The detail pane shows a larger standard-cycle response.
After selecting a material, the material page calculates its individual response.
The Brace Assembly page calculates the complete connected model using a standard
cycle or the project's current loading protocol.

The library is deliberately local rather than downloaded at runtime: projects
remain reproducible if the website changes or the computer is offline. Library
values are executable starting points, not design recommendations. Entries
marked **Requires transient / rate-dependent protocol** can be parsed by the
current solver, but their damping parameters cannot be calibrated correctly by
the current static displacement-control workflow. A false static viscous curve
is therefore not displayed for the `Viscous` material.

## UI

```powershell
python -m material_test.app
```

The UI opens the default TwoStage demo if the bundled `OpenSees.exe` and `lib\tcl8.6` are present.

To load the retained legacy case, use `File -> Load Project` and select `examples\projects\figure_1_selfcentering.yaml`.

## Material Template Format

Material support is explicit and template-driven:

```yaml
name: TwoStage
command: "uniaxialMaterial TwoStage {tag} {f1} {k1} {kp1} {f2} {k2} {kp2} {ua}"
tag: 1
parameters:
  - name: f1
    value: 270.0
    min: 0.0
```

Custom materials can be used by writing their OpenSees command template and parameter list. The program does not try to inspect or discover custom materials automatically.

Composite materials can be written as multi-line templates. Define the child materials first, then define the final tested material with `{tag}`:

```yaml
name: ParallelElastic
command: |
  uniaxialMaterial Elastic 101 {k1}
  uniaxialMaterial Elastic 102 {k2}
  uniaxialMaterial Parallel {tag} 101 102
tag: 1
parameters:
  - name: k1
    value: 100.0
  - name: k2
    value: 50.0
```

Use the same pattern for `Series`:

```yaml
command: |
  uniaxialMaterial Elastic 101 {k1}
  uniaxialMaterial Elastic 102 {k2}
  uniaxialMaterial Series {tag} 101 102
```

The material with `tag` is the one connected to the test element. Child material tags such as `101` and `102` only need to be unique inside that template.

## Loading From Experiment History

For cases where the experimental displacement history should drive the OpenSees test directly, use `loading.history`:

```yaml
loading:
  history:
    path: ../data/figure_1_experiment.txt
    displacement_column: 0
    append_zero: true
```

Whitespace-separated `.txt` files without headers are supported. Columns are named `0`, `1`, and so on.

## Tests

```powershell
python -m unittest discover -s tests
```

If `pytest` is installed, the same tests can be run with:

```powershell
python -m pytest
```
