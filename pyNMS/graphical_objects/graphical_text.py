from graphical_objects.graphical_shape import GraphicalShape
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

class GraphicalText(QGraphicsTextItem, GraphicalShape):
    
    class_type = 'shape'
    subtype = 'text'
    
    default_font = QFont()
    default_font.setFamily('Courier New')
    default_font.setPointSize(24)
    default_font.setBold(True)

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.controller = view.controller
        self.setFont(self.default_font)
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)

    def focusOutEvent(self, event):
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        super().focusOutEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.textInteractionFlags() == Qt.NoTextInteraction:
            self.setTextInteractionFlags(Qt.TextEditorInteraction)
        super().mouseDoubleClickEvent(event)