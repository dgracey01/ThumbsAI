"""
theme.py — Color constants and global QSS for ThumbsAI
Designed by: Zero  |  Built by: Jarvis
"""

# ── Palette ───────────────────────────────────────────────────────────────────
BG  = "#1a1a2e"
PAN = "#242424"
CAR = "#16213e"
ACC = "#1f6feb"
GRN = "#2ea043"
RED = "#da3633"
MUT = "#444466"
PRI = "#ffffff"
SEC = "#a0a0a0"
AMB = "#EF9F27"

FONT       = "Consolas"
FONT_SM    = 9
FONT_MD    = 11
FONT_LG    = 13
FONT_XL    = 16

VERSION   = "1.0"
SIGNATURE = f"Designed by: Zero  |  Built by: Jarvis (v{VERSION})"

# ── Global QSS ────────────────────────────────────────────────────────────────
GLOBAL_QSS = f"""
QMainWindow, QDialog {{
    background-color: {BG};
    color: {PRI};
    font-family: {FONT};
    font-size: {FONT_MD}px;
}}
QWidget {{
    background-color: transparent;
    color: {PRI};
    font-family: {FONT};
    font-size: {FONT_MD}px;
}}
QScrollArea {{ border: none; background-color: transparent; }}
QScrollBar:vertical {{
    background: {CAR}; width: 8px; border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {MUT}; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACC}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {CAR}; height: 8px; border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {MUT}; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ACC}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QPushButton {{
    background-color: {ACC}; color: {PRI}; border: none;
    border-radius: 6px; padding: 6px 16px;
    font-family: {FONT}; font-size: {FONT_MD}px; font-weight: bold;
}}
QPushButton:hover   {{ background-color: #185FA5; }}
QPushButton:pressed {{ background-color: #0d4a8a; }}
QPushButton:disabled {{ background-color: {MUT}; color: {SEC}; }}
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {CAR}; color: {PRI};
    border: 2px solid {MUT}; border-radius: 6px;
    padding: 4px 8px; font-family: {FONT}; font-size: {FONT_MD}px;
    selection-background-color: {ACC};
}}
QLineEdit:focus, QTextEdit:focus {{ border-color: {ACC}; }}
QComboBox {{
    background-color: {CAR}; color: {PRI};
    border: 2px solid {MUT}; border-radius: 6px;
    padding: 4px 8px; font-family: {FONT}; font-size: {FONT_MD}px;
    min-height: 28px;
}}
QComboBox:focus {{ border-color: {ACC}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {CAR}; color: {PRI};
    border: 1px solid {ACC}; selection-background-color: {ACC}; outline: none;
}}
QSplitter::handle {{ background: {MUT}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical   {{ height: 2px; }}
QToolTip {{
    background: {CAR}; color: {PRI}; border: 1px solid {ACC};
    padding: 4px 8px; font-family: {FONT}; font-size: {FONT_SM}px;
}}
QLabel {{ color: {PRI}; font-family: {FONT}; }}
QSlider::groove:horizontal {{
    background: {CAR}; height: 4px; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACC}; width: 14px; height: 14px;
    border-radius: 7px; margin: -5px 0;
}}
QSlider::sub-page:horizontal {{ background: {ACC}; border-radius: 2px; }}
"""

def apply_theme(app):
    app.setStyleSheet(GLOBAL_QSS)
