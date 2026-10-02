# Material Test

Python-first OpenSees material testing and material-parameter fitting tool.

## 当前工作流（2026-09-27）

**项目与单位 → 试验与加载 → 材料 → 支撑装配 → 对比与标定（手动 / 自动）→ 发布**

- **无试验**：可输入已有参数、计算滞回、设置基准、导出和发布受支持的可执行本构；RMSE 显示不适用，自动拟合禁用。
- **有试验**：支持位移、力的有符号换算系数；新导入试验默认采用试验位移加载。只有顺序一致的位移历程才计算误差，不按滞回横坐标插值；求解器补入的初始零点在误差对齐时去除。
- **全局加载**：幅值序列、外部文件、试验位移三选一，材料预览、装配预览和正式计算共用。旧项目未声明来源时，保留其原来的文件/幅值选择。
- **单位与模式**：先声明 N/kN/MN 和 mm/cm/m。新项目默认力–变形直接输入（A=L=1）；直接输入使用轴向 twoNodeLink，串联单元不会被长度权重再次缩放。旧项目保持应力–应变模式，使用 Truss 和面积、长度换算。切换单位只声明输入含义，不自动修改已有参数。
- **参数与拟合**：两处同步编辑，可启用自动重算。拟合边界按当前值的 0.5～2 倍排序后预填，并限制在材料允许区间；零值使用允许范围内的初始区间，需用户核对。拟合支持进度、取消、应用前对比、放弃与撤销；应用后自动完整复算。
- **保存与状态**：Ctrl+S 保存当前路径，Ctrl+Shift+S 另存为；未保存显示星号，关闭及打开另一项目有保存保护。替换材料列出将解除赋值的单元并确认。过期计算显示灰色虚线和黄色重算入口，不能作为基准、导出或发布。
- **图表**：GUI 统一用 PyQtGraph，中文坐标轴带单位；试验黑色、基准灰色虚线、当前蓝色。空图显示说明。单材及装配预览可叠加试验数据。PNG/PDF/SVG 导出保持 300 dpi 和论文图刻度，图例置于绘图区外。
- **加载模板**：等幅、逐级递增以及可增删转折点的表格编辑。特定规范协议应按明确的规范版本录入或导入；通用循环模板不冒称规范协议。
- **后台任务**：拟合与正式计算可取消；关闭运行中的项目会先取消任务再完成保存确认。材料库按需后台预览并缓存。所有页面共用可展开日志。

界面定义位于 material_test/ui/pages；项目状态和加载控制位于
project_session.py，计算、拟合与发布控制位于 fitting_workflow.py。
本构仍需下游有对应可执行实现；未支持类型明确拒绝发布，不以曲线冒充本构。

## Publish an executable brace

After a complete calculation, use **发布支撑模型…** on **对比与标定** or
**Ctrl+Shift+P**. Units come from **项目与单位**. Save an .oasbrace package
and import it on Structural analysis's component assembly page. Experimental
measurements are optional: numerical-only packages explicitly declare
manual-assumption / “参数直接输入、未经试验标定”. Packages with experimental
data retain its source checksum and conversion factors; merely attaching data
does not claim that calibration was completed.

The v2 package preserves two axial terminals, nested explicit Series/Parallel groups,
supported material laws (Elastic, Steel01, Steel02, SelfCentering and MinMax wrappers), original
curves, and input/result SHA-256 values. Topology groups become explicit branch elements
and massless internal coordinates in Structural analysis; they are not silently replaced
by a composite Series material. This is a fixed-axis, quasi-static internal mechanism
interface, not general spatial contact or internal inertial dynamics. Unsupported material
commands are rejected instead of replaced with a fitted curve.

Changed inputs, changed results and incomplete loading runs cannot be published. After
parameter fitting, review and apply the proposed values. The UI automatically reruns
the full selected loading history and enables publication after successful verification. Area and length must be consistent with the declared units; A=L=1 may be used
only when the material command is already an equivalent force–deformation law.

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

### Shared structural component tests

Structural analysis now provides a first-party component workbench with real
elasticBeamColumn / twoNodeLink assemblies and inline tests. Material_Test can
evaluate its exported .oasbrace packages through the same component kernel:

~~~powershell
python -m material_test.cli component-test component.oasbrace --path "0,10,-10,0" --step 0.5 --output new-component-test.json
~~~

The output must be a new file. The result includes the resolved component,
loading history, internal responses, balance checks, source identity and solver
identity. The thin adapter is material_test/component_adapter.py; it requires
Structural analysis in the same workspace and runs a fresh solver process.
Advanced studies can call evaluate_component with explicit parameter overrides.
The existing material fitting workflow remains available.

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

In the desktop UI, choose the global algorithm on the **对比与标定 / 自动拟合** tab,
select the parameters to fit, and provide both Min and Max values. The normal
**Run** action remains available for checking a single parameter set.

The fifth workflow page, **对比与标定 / 手动调参**, is the manual calibration
workspace. It edits the selected material parameters without returning to the
material-library page, runs the current model, and overlays the experiment,
baseline, and current responses. The current result can be frozen as a baseline
before another parameter change. Peak force, initial stiffness, hysteretic
integral, endpoint force, RMSE, and normalized RMSE are shown together; error
metrics are reported only when the calculation and experiment use the same
displacement sequence. The calculation can use either the configured loading
protocol or the imported experimental displacement history. Export lives on this page; the expandable bottom run log is available throughout the application.
Material-value editors accept decimal or scientific notation such as `1e6` and
`-2.5e-3`. The on-screen comparison uses an interactive PyQtGraph canvas with
direct wheel zoom, mouse-drag panning, context-menu controls, and auto-range
reset. **Export Current Comparison** writes PNG, PDF, or SVG with the publication
Matplotlib style: Times New Roman/SimSun, major inward ticks, explicit tick-bound
limits, 25-point axis labels, no title, and 300-dpi raster output.

## Material Resources and Brace Assembly

The desktop UI is organized as a five-step workflow: project and units, optional experiment and loading, materials, brace assembly, and comparison/calibration. **Material Library &
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

The **组合与包装** category also includes **Series**, **Parallel**, and **MinMax**.
To add Series or Parallel, select at least two existing child materials in the
library dialog. For MinMax, select one existing material; edit its `minStrain`
and `maxStrain` limits on **Calculation & Comparison**. The new material gets
its own unique Tag and can be assigned to a brace element like any other
resource. Child definitions remain before the wrapper in the saved command.
Referenced child materials cannot be deleted until their wrappers are removed.

For an empty project, **使用示例替换材料集合** loads a complete example with
child materials. Library previews use these examples, while a material added
to the project references the children selected by the user. MinMax failure
is irreversible during a loading run. Series/Parallel materials combine
constitutive laws within an element; use **Brace Assembly** when separate
element areas, lengths, or physical connection topology are required.

Use **Replace Material Set** to start a new material resource set, or **Add to
Material Resources** to append a uniquely tagged material. Assign resources to
elements later on the **Brace Assembly** page. Added parameters are automatically
namespaced, and the combined command is stored inline in the project YAML, so no
separate material-template file is required.

Each defined `uniaxialMaterial` command appears as a selectable row on the
material page. Clicking a row, or stepping through the loaded values in the
material Tag control, opens that material's current parameter description.
Material-library defaults are inserted automatically. Values are editable on both
**材料** and **对比与标定** and synchronize immediately. Commands show rendered
values instead of placeholders. The optional 400 ms debounce switch recalculates
the current model after parameter changes.
Metadata includes the Chinese meaning, unit, and the part of the hysteresis
response it primarily controls.

Library previews are generated lazily in background jobs by the configured
OpenSees executable and cached by solver, command and geometry. The detail pane shows a larger standard-cycle response.
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

The UI opens an empty material project with the bundled solver configured.
The TwoStage demo is numerical-only and needs no experimental sample CSV.

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
