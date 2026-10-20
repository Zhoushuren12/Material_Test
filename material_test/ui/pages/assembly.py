from PySide6.QtWidgets import QPushButton, QStyle, QVBoxLayout, QWidget

from material_test.ui.assembly_editor import AssemblyEditor


class AssemblyPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        window.topology_editor = AssemblyEditor()
        window.topology_editor.changed.connect(window._on_topology_changed)
        window.calculate_model_preview_button = QPushButton('计算滞回曲线')
        window.calculate_model_preview_button.setObjectName('primaryButton')
        window.calculate_model_preview_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        window.calculate_model_preview_button.clicked.connect(window.calculate_model_preview)
        window.topology_editor.command_layout.addWidget(window.calculate_model_preview_button,1)
        window.assembly_deformation_button = QPushButton('阻尼器变形图')
        window.assembly_deformation_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        window.assembly_deformation_button.setToolTip('计算滞回曲线后查看各节点的位移与转角')
        window.assembly_deformation_button.setEnabled(False)
        window.assembly_deformation_button.clicked.connect(window.open_assembly_deformation)
        window.topology_editor.command_layout.addWidget(window.assembly_deformation_button,1)
        layout.addWidget(window.topology_editor)
