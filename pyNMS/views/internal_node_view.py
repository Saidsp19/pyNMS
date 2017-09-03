from .base_view import BaseView

from networks.graph import Graph

class InternalNodeView(BaseView):
    
    subtype = 'innode'

    def __init__(self, gnode, controller):
        self.gnode = gnode
        self.node = gnode.node
        self.network = Graph(self)
        super().__init__(controller)
        controller.current_project.hlayout.addWidget(self)
        self.hide()
        
    def dropEvent(self, event):
        pos = self.mapToScene(event.pos())
        if event.mimeData().hasFormat('application/x-dnditemdata'):
            from graphical_objects.graphical_network_node import GraphicalNetworkNode
            new_gnode = GraphicalNetworkNode(self)
            new_gnode.setPos(pos)