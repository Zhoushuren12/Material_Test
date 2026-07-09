# Material Test

Python-first OpenSees material testing and manual fitting tool.

The first version supports a practical manual-fitting loop:

1. Select an OpenSees executable.
2. Load an explicit material command template.
3. Edit material parameter values.
4. Define cyclic displacement loading.
5. Import experiment displacement-force data.
6. Run OpenSees through Python.
7. Overlay experiment and simulation curves for manual judgment.

Automatic optimization, batch parameter studies, and automatic custom-material discovery are intentionally out of scope for the first version.

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
