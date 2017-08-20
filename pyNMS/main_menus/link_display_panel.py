from miscellaneous.decorators import update_paths
from objects.objects import *
from os.path import join
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtGui import (
                         QColor, 
                         QDrag, 
                         QIcon,
                         QPainter, 
                         QPixmap
                         )
from PyQt5.QtWidgets import (
                             QFrame,
                             QPushButton, 
                             QWidget, 
                             QApplication, 
                             QLabel, 
                             QGraphicsPixmapItem,
                             QGroupBox,
                             )

class LinkDisplayPanel(QGroupBox):
    
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.setTitle('Link creation')
                
        # exit connection lost because of the following lines
        layout = QtWidgets.QGridLayout(self)
        for index, subtype in enumerate(link_subtype):
            if subtype in ('l2vc', 'l3vc'):
                continue
            button = QPushButton(self)
            button.setStyleSheet('QPushButton{ background-color: rgb(255, 255, 255);}')
            button.clicked.connect(lambda _, s=subtype: self.change_display(s))
            image_path = join(controller.path_icon, subtype + '.png')
            button.setIcon(QIcon(image_path))
            button.setIconSize(QtCore.QSize(120, 30))
            layout.addWidget(button, index // 2, index % 2, 1, 1)
            
    @update_paths
    def change_display(self, subtype):
        self.view.per_subtype_display(subtype)   
