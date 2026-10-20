"""Render the assembly page and exercise the actual curve-dialog calculation."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--show',action='store_true')
    args=parser.parse_args()
    if not args.show:
        os.environ['QT_QPA_PLATFORM']='offscreen'
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    from material_test.io.project_config import load_project_config
    from material_test.ui.main_window import MainWindow

    app=QApplication([])
    for font in ('msyh.ttc','msyhbd.ttc','segoeui.ttf','consola.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
    model=ROOT.parents[1]/'模型配置'/'材料模型'/'Material_Test_支撑显式装配_交互验收_试算.yaml'
    w=MainWindow(restore_last_project=False); w._populate_from_config(load_project_config(model))
    w.current_project_path=model; w._project_dirty=False; w._update_project_title()
    w.resize(1460,900); w.show(); w._navigate_to(3)
    w.topology_editor.canvas.select('element',1)
    if args.show:
        return app.exec()
    output=ROOT/'outputs'/'assembly_acceptance'/'visual_qa'; output.mkdir(parents=True,exist_ok=True)
    app.processEvents(); w.topology_editor.canvas.fit_all(); app.processEvents()
    for node in w.topology_editor.graph['nodes']:
        from PySide6.QtCore import QPointF
        canvas=w.topology_editor.canvas
        assert canvas.viewport().rect().contains(canvas.mapFromScene(QPointF(node['x'],-node['y'])))
    w.grab().save(str(output/'assembly_desktop.png'))
    w.resize(1100,700); app.processEvents(); w.topology_editor.canvas.fit_all(); app.processEvents()
    for node in w.topology_editor.graph['nodes']:
        assert canvas.viewport().rect().contains(canvas.mapFromScene(QPointF(node['x'],-node['y'])))
    w.grab().save(str(output/'assembly_compact.png'))
    w.resize(1460,900); app.processEvents(); w.open_assembly_response()
    loop=QEventLoop(); timer=QTimer(); timer.setInterval(20)
    timer.timeout.connect(lambda:loop.quit() if w._thread is None else None)
    timeout=QTimer(); timeout.setSingleShot(True); timeout.timeout.connect(loop.quit)
    timer.start(); timeout.start(60000); loop.exec(); timer.stop(); timeout.stop()
    if w._thread is not None:
        w.cancel_analysis()
        raise RuntimeError('QA calculation exceeded timeout')
    if w.current_result is None:
        raise RuntimeError(w.log_edit.toPlainText())
    app.processEvents(); w.assembly_response_dialog.grab().save(str(output/'hysteresis_dialog.png'))
    w.open_assembly_deformation(); app.processEvents()
    deformation = w.assembly_deformation_dialog
    import numpy as np
    data = deformation.history
    peak = int(np.argmax(np.linalg.norm(data.displacements[:, :, :2], axis=2).max(axis=1)))
    before = dict(deformation.canvas.positions)
    deformation.seek(peak); app.processEvents()
    assert deformation.canvas.positions != before
    assert deformation.export_image(output/'deformation_export.png')
    deformation.grab().save(str(output/'deformation_desktop.png'))
    deformation.resize(680,500); app.processEvents()
    deformation.canvas.fit_all(); app.processEvents()
    for point in deformation.canvas.positions.values():
        assert deformation.canvas.viewport().rect().contains(deformation.canvas.mapFromScene(point))
    deformation.grab().save(str(output/'deformation_compact.png'))
    deformation.toggle_play(); assert deformation.timer.isActive()
    deformation.hide(); assert not deformation.timer.isActive()
    print('Model:',model)
    print('Run:',w.current_result.run_dir)
    print('Samples:',len(w.current_result.simulation.force))
    print('Window:',w.width(),w.height())
    w._project_dirty=False; w.close()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
