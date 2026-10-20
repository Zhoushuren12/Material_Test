"""Shared visual styling for the material testing workspace."""

STYLESHEET = """
    QWidget { color: #283638; font-family: 'Microsoft YaHei UI'; }
    QMainWindow, #appRoot { background: #f5f7f7; }
    QLabel { background: transparent; }
    QDialog, QMenu { background: #ffffff; }
    QMenu { border: 1px solid #dbe3ed; padding: 6px; }
    QMenu::item { padding: 7px 20px; border-radius: 4px; }
    QMenuBar { background: #ffffff; border-bottom: 1px solid #e6ebf2; padding: 3px 12px; }
    QMenuBar::item:selected, QMenu::item:selected { background: #edf4ff; color: #2563ae; }
    #appHeader { background: #ffffff; border-bottom: 1px solid #e2e8f0; }
    #brandMark { min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px;
        background: #167d70; color: #ffffff; border-radius: 6px; font-weight: 700; }
    #brandTitle { font-size: 18px; font-weight: 700; color: #1e304a; }
    #projectLabel { color: #7b889c; padding-left: 8px; }
    #navigation { background: #edf1f0; border: none; border-right: 1px solid #dce3e1; }
    #navCaption { color: #71817d; font-size: 11px; font-weight: 600; padding: 0 10px 10px 10px; }
    #navList { background: transparent; color: #596965; outline: none; border: none; }
    #navList::item { padding: 14px 10px; margin: 3px 0; border: 1px solid transparent; border-radius: 6px; }
    #navList::item:selected { background: #ffffff; color: #12695e; border: 1px solid #cbded8; }
    #navList::item:hover:!selected { background: #e3eae7; }
    #statusPill { background: #e1e9e5; color: #506b61; padding: 10px 8px; border-radius: 6px; }
    #pageTitle { font-size: 22px; font-weight: 700; color: #253735; }
    #pageSubtitle { color: #7b889c; font-size: 12px; padding-bottom: 6px; }
    #sectionTitle { font-size: 14px; font-weight: 600; color: #2b405c; }
    #surfaceCard { background: #ffffff; border: none; border-radius: 0; }
    #scrollContent { background: #ffffff; border: none; }
    #pageCounter, #mutedLabel { color: #8290a4; font-size: 11px; }
    #typeBadge { color: #527398; background: #edf3fa; border-radius: 5px; padding: 4px 8px; }
    #sourceBadge { color: #64809f; background: #f1f5fa; border-radius: 6px; padding: 7px 10px; }
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
        background: #ffffff; border: 1px solid #d6dfeb; border-radius: 6px;
        selection-background-color: #dceaff; selection-color: #254d7c; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 30px; padding: 0 8px; }
    #compactField { min-height: 26px; padding: 0 7px; }
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
        border-color: #729ad0; }
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
        background: #f5f7fa; color: #a0aabb; border-color: #e5eaf1; }
    QPlainTextEdit { padding: 8px; }
    QComboBox::drop-down { width: 22px; border: none; }
    QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #edf4ff; }
    QTableWidget, QTreeWidget { background: #ffffff; border: 1px solid #e3e9f1; border-radius: 7px;
        gridline-color: #edf1f6; alternate-background-color: #f8fafc;
        selection-background-color: #e9f2ff; selection-color: #254d7c; outline: none; }
    QTreeWidget::item { min-height: 30px; padding: 2px 0; }
    QTreeWidget::item:selected { background: #e9f2ff; color: #254d7c; }
    QTreeWidget::item:hover:!selected { background: #f2f6fb; }
    QHeaderView::section { background: #f4f7fb; color: #708096; border: none;
        border-bottom: 1px solid #e2e9f2; padding: 9px 7px; font-size: 11px; font-weight: 600; }
    QTabWidget::pane { border: 1px solid #dfe6e3; background: #ffffff; border-radius: 4px; }
    QTabBar::tab { background: #edf1f0; color: #64766e; padding: 10px 12px;
        border: none; border-bottom: 2px solid transparent; margin-right: 2px; }
    QTabBar::tab:selected { background: #ffffff; color: #167d70; border-bottom: 2px solid #167d70; font-weight: 600; }
    #workflowModes::tab { background: #eef2f1; color: #6a7b75; padding: 8px 14px;
        border: 1px solid #e0e6e3; margin: 0; min-width: 92px; }
    #workflowModes::tab:selected { background: #e0f0e9; color: #116656; border: 1px solid #b7d3c7; }
    QPushButton, QToolButton { background: #ffffff; border: 1px solid #d6dfeb; border-radius: 6px;
        min-height: 32px; padding: 0 12px; }
    QPushButton:hover, QToolButton:hover { background: #f0f5fc; border-color: #abc2df; }
    QPushButton:pressed, QToolButton:pressed { background: #e6eef8; }
    QPushButton:disabled, QToolButton:disabled { color: #a0adbd; background: #f2f5f9; border-color: #e2e8f0; }
    #primaryButton { background: #167d70; color: #ffffff; border-color: #167d70; font-weight: 600; }
    #primaryButton:hover { background: #11695e; border-color: #11695e; }
    #primaryButton:pressed { background: #0d564d; }
    #primaryButton:disabled { background: #aebfd5; border-color: #aebfd5; color: #f6f8fc; }
    #dangerButton, #dangerTool { color: #b55d58; border-color: #e8d5d3; }
    #dangerButton:hover, #dangerTool:hover { background: #fff3f1; border-color: #daa7a2; }
    #compactButton, #dangerButton { min-height: 28px; padding: 0 9px; }
    QToolButton { min-width: 26px; padding: 0 8px; }
    #canvasTool { min-height: 26px; min-width: 20px; padding: 0 7px; border-color: #e1e7f0; }
    QSplitter::handle { background: transparent; }
    QSplitter::handle:hover { background: #dbe6f4; border-radius: 4px; }
    QSplitter#materialPageSplitter::handle, QSplitter#materialDetailSplitter::handle,
    QSplitter#assemblyPageSplitter::handle, QSplitter#assemblyInspectorSplitter::handle {
        background: #e0e8e5; border-radius: 4px; }
    QSplitter#materialPageSplitter::handle:hover, QSplitter#materialDetailSplitter::handle:hover,
    QSplitter#assemblyPageSplitter::handle:hover, QSplitter#assemblyInspectorSplitter::handle:hover {
        background: #abcfc3; }
    QScrollBar:vertical { width: 9px; background: transparent; margin: 3px 1px; }
    QScrollBar:horizontal { height: 9px; background: transparent; margin: 1px 3px; }
    QScrollBar::handle { background: #c9d4e2; border-radius: 4px; min-height: 24px; min-width: 24px; }
    QScrollBar::handle:hover { background: #a7bad2; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QCheckBox { spacing: 7px; }
    #runState { color: #64748b; font-weight: 600; }
    #previewNote { color: #7b8a9f; padding: 4px 2px; font-size: 11px; }
    #componentWorkspace { background: transparent; }
    #assemblyToolbar { background: transparent; border: none; spacing: 3px; }
    #assemblyToolbar QToolButton { min-width: 22px; max-width: 22px; min-height: 30px; padding: 0 3px; }
    #assemblyToolbar QToolButton:checked { background: #dceee8; border-color: #86b7a7; }
    #assemblyInspector QScrollArea, #assemblyInspectorContent { background: transparent; }
    #assemblyInspector QLineEdit, #assemblyInspector QComboBox, #assemblyInspector QDoubleSpinBox { min-height: 26px; }
    #assemblyCanvas { border: 1px solid #d5dfdc; }
    #componentScroll, #componentForm { background: #f5f7f7; border: none; }
    #componentSummary { color: #64766e; border-top: 1px solid #dce5e1; padding-top: 18px; }
    #runState[state="stale"] { color: #a16207; }
    #runState[state="running"] { color: #167d70; }
    #runState[state="complete"] { color: #15803d; }
    #runState[state="failed"] { color: #b91c1c; }
    QSlider::groove:horizontal { height: 4px; background: #dce4df; border-radius: 2px; }
    QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #167d70; border-radius: 6px; }
    QToolTip { background: #283f36; color: #ffffff; border: none; padding: 7px; }
"""
