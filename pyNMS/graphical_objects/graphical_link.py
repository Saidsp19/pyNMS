from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from right_click_menus.internal_site_selection_menu import InternalSiteSelectionMenu
from right_click_menus.main_network_selection_menu import MainNetworkSelectionMenu

class GraphicalLink(QGraphicsLineItem):
    
    def __init__(self, view, link=None):
        super().__init__()
        self.view = view
        self.controller = view.controller
        self.setFlag(QGraphicsItem.ItemIsSelectable, self.view.selection['link'])
        if link:
            self.link = link
            # source and destination graphic nodes
            self.source = link.source.gnode[view]
            self.destination = link.destination.gnode[view]
        else:
            self.destination = view.end_node
            self.source = view.start_node
            self.link = view.network.lf(
                                        subtype = view.controller.creation_mode,
                                        source = self.source.node, 
                                        destination = self.destination.node
                                        )
        self.object = self.link
        self.setZValue(2)
        self.setPen(view.link_color[self.link.subtype])
        self.update_position()
        view.scene.addItem(self)
        self.link.glink[view] = self.link.gobject[view] = self
        
    @property
    def is_selectable(self):
        # for a link to be selectable, the selection mode must be one, and the
        # link selection must be activated (display menu)
        selection_allowed = self.controller.mode == 'selection'
        link_selection_allowed = self.view.selection['link']
        return selection_allowed and link_selection_allowed
        
    def mousePressEvent(self, event):
        # ideally, the menu should be triggered from the mouseReleaseEvent
        # binding, but for QT-related issues, the right-click filter does not
        # work in mouseReleaseEvent
        if event.buttons() == Qt.RightButton:
            # we set the item selectability to True, no matter what the actual
            # selection mode is, because we want the user to be able to trigger
            # the right-click menu at all times
            # eventually, we will rollback this change if needed depending on 
            # the selection mode
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setSelected(True)
            menu = {
                    'network': MainNetworkSelectionMenu,
                    'internal': InternalSiteSelectionMenu,
                    }[self.view.subtype](self.controller)
            menu.exec_(QCursor.pos())
            self.setFlag(QGraphicsItem.ItemIsSelectable, self.is_selectable)
            
    def itemChange(self, change, value):
        if change == self.ItemSelectedHasChanged:
            if self.isSelected():
                self.setPen(self.view.selection_pen)
            else:
                self.setPen(self.view.link_color[self.link.subtype])
        return QGraphicsLineItem.itemChange(self, change, value)
        
    def update_position(self):
        start_position = self.source.pos()
        end_position = self.destination.pos()
        self.setLine(QLineF(start_position, end_position))
        
    def self_destruction(self):
        self.view.scene.removeItem(self)