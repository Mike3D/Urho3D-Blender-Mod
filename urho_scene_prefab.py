#
# This script is licensed as public domain.
#

bl_info = {
    'name': 'Export Urho3D Scene Prefab',
    'author': 'Mike3D',
    'version': (1,0),
    'blender': (2, 77, 0),
    'location': 'Properties > Render > Urho3D Scene Prefab',
    'description': 'Export Urho3D scene prefab',
    'warning': '',
    'category': 'Import-Export'}


import bpy
from bpy_extras.io_utils import axis_conversion
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, Panel, Scene
import logging
from math import degrees, radians
from mathutils import Euler, Matrix, Quaternion, Vector
from os.path import exists
from xml.etree import ElementTree as ET

from io_mesh_urho.utils import CheckFilepath, FOptions, GetFilepath, PathType, WriteXmlFile
from io_mesh_urho.export_scene import Tree, UrhoScene

log = logging.getLogger('ExportLogger')


#------------------------
# Bit masks stuff
#------------------------
MAX_BITMASK_BITS = 8
MAX_BITMASK_VALUE = (1 << MAX_BITMASK_BITS) - 1

def GetBitMask(layers):
    mask = 0
    for i, bit in enumerate(layers):
        mask = mask | (1 << i if bit else 0)

    if mask == MAX_BITMASK_VALUE:
        mask = -1

    return mask


class UrhoInstance:
    def __init__(self):
        # Prefab name
        self.name = None
        # Prefab parent name
        self.parentName = None
        # Prefab matrix
        self.matrix = None
        # Object name
        self.objectName = None

instances = [] # List of UrhoInstance
mdls = [] # List of model names


def UrhoGatherInstances(obj):

    # Compute transforms and append instance to the list of models to process
    def UrhoDupliTransforms(dupli, root, parent = None):

        if dupli.matrix_local.to_euler() != Euler((0.0, 0.0, 0.0)):
            log.warning('You should apply rotation to object {:s}'.format(dupli.name))
        if dupli.matrix_local.to_scale() != Vector((1.0, 1.0, 1.0)):
            log.warning('You should apply scale to object {:s}'.format(dupli.name))

        instance = UrhoInstance()
        instance.name = dupli.name
        instance.matrix = root.matrix_world
        instance.objectName = obj.name

        if parent:
            instance.matrix = instance.matrix * parent.matrix_world

        # Parent-child relationship inside a group object
        if dupli.children:
            instance.objectName += dupli.name # Make parent name unic for parent-child relationship

        if dupli.parent and dupli.parent.type != 'ARMATURE':
            instance.parentName = obj.name + dupli.parent.name # See parent naming above
            instance.matrix = dupli.matrix_local

        instances.append(instance)

    if obj.hide:
        return

    if obj.type == 'EMPTY' and obj.dupli_group: # Look for instances
        for dupli in obj.dupli_group.objects: # Find members for this group
            if dupli.hide or dupli.type == 'ARMATURE': continue

            if dupli.type == 'EMPTY' and dupli.dupli_group: # Bunch of instances found
                for subDupli in dupli.dupli_group.objects:
                    UrhoDupliTransforms(subDupli, obj, dupli)
            else:
                UrhoDupliTransforms(dupli, obj)

    elif obj.type == 'MESH': # Look for mesh objects
        instance = UrhoInstance()
        instance.name = obj.data.name # Use mesh name instead of object name
        instance.matrix = obj.matrix_world
        instance.objectName = obj.name

        if obj.parent and obj.parent.type == 'MESH':
            instance.parentName = obj.parent.name
            instance.matrix = obj.matrix_local

        instances.append(instance)


#------------------------
# Export scene prefab
#------------------------
class ExportScenePrefabButton(Operator):
    bl_idname = 'urho_scene_prefab.export'
    bl_label = 'Export Button'
    bl_description = 'Export Urho3D scene prefab'
    bl_options = {'UNDO'}

    # From export_scene.py
    def SortModels(self, context):
        models = [ model for model in context.scene.objects if not model.hide ]
        sortedModels = []

        # Sort by parent-child relation
        names_tree = Tree()
        for model in models:
            names_tree.push((model.name, model.parent.name if model.parent else None))

        # Rearrange the model list in the new order
        for name in names_tree.to_list():
            for model in models:
                if model.name == name:
                    sortedModels.append(model)
                    # No need to reverse the list, we break straightway
                    models.remove(model)
                    break

        return sortedModels

    def GetPrefabRoot(self, name, fOptions):
        prefabFile = GetFilepath(PathType.OBJECTS, name, fOptions) [0]
        if not exists(prefabFile):
            log.critical('Cannot find prefab: {:s} '.format(prefabFile))
            return None

        # Get prefab content
        prefab_root = ET.parse(prefabFile).getroot()
        if prefab_root.tag != 'node':
            log.critical('Invalid prefab: {:s} '.format(prefabFile))
            return None

        return prefab_root

    def execute(self, context):

        scn = context.scene
        options = scn.urho_exportsettings

        # File utils options
        fOptions = FOptions()
        fOptions.useSubDirs = options.useSubDirs
        fOptions.fileOverwrite = options.fileOverwrite
        fOptions.paths[PathType.ROOT] = options.outputPath
        fOptions.paths[PathType.OBJECTS] = options.objectsPath
        fOptions.paths[PathType.SCENES] = options.scenesPath

        elements = {} # Map node xml element to model name
        nodeID = 0x1000000  # node ID
        compoID = nodeID    # component ID

        # Create scene components
        sceneRoot = ET.Element('scene')
        sceneRoot.set('id', '1')

        octreeElem = ET.SubElement(sceneRoot, 'component')
        octreeElem.set('type', 'Octree')
        octreeElem.set('id', '1')

        debugElem = ET.SubElement(sceneRoot, 'component')
        debugElem.set('type', 'DebugRenderer')
        debugElem.set('id', '2')

        # Skybox - TODO: warn if file not found
        if scn.urho_skyboxPath != '':
            skyboxElem = ET.SubElement(sceneRoot, 'component')
            skyboxElem.set('type', 'Skybox')
            skyboxElem.set('id', '4')

            skyboxModelElem = ET.SubElement(skyboxElem, 'attribute')
            skyboxModelElem.set('name', 'Model')
            skyboxModelElem.set('value', 'Model;Models/Box.mdl')

            skyboxMaterialElem = ET.SubElement(skyboxElem, 'attribute')
            skyboxMaterialElem.set('name', 'Material')
            skyboxMaterialElem.set('value', 'Material;{:s}'.format(scn.urho_skyboxPath))

        if scn.urho_navigation:
            navigationElem = ET.SubElement(sceneRoot, 'component')
            navigationElem.set('type', 'NavigationMesh')
            navigationElem.set('id', '5')

        if scn.urho_physics:
            physicsElem = ET.SubElement(sceneRoot, 'component')
            physicsElem.set('type', 'PhysicsWorld')
            physicsElem.set('id', '6')

        lightNodeElem = ET.SubElement(sceneRoot, 'node')
        lightNodeElem.set('id', '{:d}'.format(nodeID))
        nodeID += 1

        lightNodeNameElem = ET.SubElement(lightNodeElem, 'attribute')
        lightNodeNameElem.set('name', 'Name')
        lightNodeNameElem.set('value', 'DirectionalLight')

        lightNodeRotElem = ET.SubElement(lightNodeElem, 'attribute')
        lightNodeRotElem.set('name', 'Rotation')
        lightNodeRotElem.set('value', '0.9 0.4 0.25 0')

        lightElem = ET.SubElement(lightNodeElem, 'component')
        lightElem.set('type', 'Light')
        lightElem.set('id', '7')

        lightTypeElem = ET.SubElement(lightElem, 'attribute')
        lightTypeElem.set('name', 'Light Type')
        lightTypeElem.set('value', 'Directional')

        # Create Root node
        root = ET.SubElement(sceneRoot, 'node')

        root.set('id', '{:d}'.format(nodeID))
        rootElem = ET.SubElement(root, 'attribute')
        rootElem.set('name', 'Name')
        rootElem.set('value', scn.name)

        # Set Root node as Navigable
        if scn.urho_navigation and scn.urho_navigable:
            navigableElem = ET.SubElement(root, 'component')
            navigableElem.set('type', 'Navigable')

        # Gather instances of prefab objects (models are sorted by parent-child relationship)
        for obj in self.SortModels(context):
            UrhoGatherInstances(obj)

        # Export each decomposed object
        for instance in instances:

            # Get prefab root xml element
            prefab_root = self.GetPrefabRoot(instance.name, fOptions)
            if not prefab_root:
                continue

            elements[instance.objectName] = prefab_root

            # Set transforms (position, rotation and scale)

            front_view = scn.urho_orientation
            if front_view == 'X_PLUS': # Right (+X +Z)
                posMatrix = axis_conversion(to_forward = 'X', to_up = 'Z').to_4x4()
                orientation = Quaternion((0.0, 0.0, 1.0), radians(90.0))
            elif front_view == 'X_MINUS': # Left (-X +Z)
                posMatrix = axis_conversion(to_forward = '-X', to_up = 'Z').to_4x4()
                orientation = Quaternion((0.0, 0.0, 1.0), radians(-90.0))
            elif front_view == None: # 'Y_PLUS' Back (+Y +Z)
                posMatrix = axis_conversion(to_forward = 'Y', to_up = 'Z').to_4x4()
                orientation = None
            elif front_view == 'Y_MINUS': # Front (-Y +Z)
                posMatrix = axis_conversion(to_forward = '-Y', to_up = 'Z').to_4x4()
                orientation = Quaternion((0.0, 0.0, 1.0), radians(180.0))
            elif front_view == 'Z_PLUS': # Top (+Z +Y)
                posMatrix = axis_conversion(to_forward = 'Z', to_up = 'Y').to_4x4()
                orientation = Quaternion((1.0, 0.0, 0.0), radians(-90.0)) * Quaternion((0.0, 0.0, 1.0), radians(180.0))
            elif front_view == 'Z_MINUS': # Bottom (-Z -Y)
                posMatrix = axis_conversion(to_forward = '-Z', to_up = '-Y').to_4x4()
                orientation = Quaternion((1.0, 0.0, 0.0), radians(90.0)) * Quaternion((0.0, 0.0, 1.0), radians(180.0))

            # Apply custom scene scaling last
            if scn.urho_scale != 1.0:
                posMatrix *= Matrix.Scale(scn.urho_scale, 4)

            nodePos = instance.matrix.to_translation() * posMatrix
            nodeRot = (orientation.to_matrix().to_4x4() * instance.matrix * posMatrix).to_euler() if orientation else (instance.matrix * posMatrix).to_euler()
            nodeScale = (instance.matrix * orientation.to_matrix().to_4x4()).to_scale() if orientation else instance.matrix.to_scale()

            pos = '{:g} {:g} {:g}'.format(nodePos[0], nodePos[2], nodePos[1])
            rot = '{:g} {:g} {:g}'.format(degrees(-nodeRot[0]), degrees(-nodeRot[2]), degrees(-nodeRot[1]))
            scale = '{:g} {:g} {:g}'.format(nodeScale[0], nodeScale[2], nodeScale[1])

            # Set new transforms and name
            for attribute in prefab_root.findall('attribute'):
                elem = attribute.get('name')

#                if elem == 'Name':
#                     attribute.set('value',  instance.objectName)

                if  elem == 'Position':
                    attribute.set('value',  pos)
                    pos = ''

                elif elem == 'Rotation':
                    attribute.set('value', rot)
                    rot = ''

                elif elem == 'Scale':
                    attribute.set('value', scale)
                    scale = ''

            # If transforms not found in the prefab (ie set to default), create them
            if pos != '':
                p = ET.SubElement(prefab_root, 'attribute')
                p.set('name', 'Position')
                p.set('value', pos)
            if rot != '':
                r = ET.SubElement(prefab_root, 'attribute')
                r.set('name', 'Rotation')
                r.set('value', rot)
            if scale != '':
                s = ET.SubElement(prefab_root, 'attribute')
                s.set('name', 'Scale')
                s.set('value', scale)

            # Append prefab instance to scene root or parent prefab element
            if instance.parentName and instance.objectName in elements:
                elements[instance.parentName].append(prefab_root)
            else:
                sceneRoot.append(prefab_root)

        # Write scene prefab file
        filepath = GetFilepath(PathType.SCENES, scn.name, fOptions)
        if CheckFilepath(filepath[0], fOptions):
            log.info( 'Creating scene prefab {:s}'.format(filepath[1]) )
            WriteXmlFile(sceneRoot, filepath[0], fOptions)

        bpy.ops.urho.report('INVOKE_DEFAULT') # Display report

        return{'FINISHED'}


###########
# Panel
###########
class ExportScenePrefabPanel(Panel):
    """ Draw Panel in Properties > Render """
    bl_idname = 'Export Urho3D scene prefab'
    bl_label = 'Urho3D Scene Prefab'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'render'

    Scene.urho_orientation = EnumProperty(
            name = 'Front view',
            description = 'Front view of the model. IMPORTANT: must be identical to the individual prefabs',
            items = (('X_MINUS', 'Left (--X +Z)', ''),
                     ('X_PLUS',  'Right (+X +Z)', ''),
                     ('Y_MINUS', 'Front (--Y +Z)', ''),
                     ('Y_PLUS',  'Back (+Y +Z) *', ''),
                     ('Z_MINUS', 'Bottom (--Z --Y)', ''),
                     ('Z_PLUS',  'Top (+Z +Y)', '')),
            default = 'X_PLUS')

    Scene.urho_scale = FloatProperty(
            name = 'Scale',
            description = 'Scale to apply to the scene. IMPORTANT: must be identical to the individual prefabs',
            default = 1.0,
            min = 0.0,
            max = 1000.0,
            step = 10,
            precision = 1)

    Scene.urho_physics = BoolProperty(
            name = 'PhysicsWorld',
            description = 'Add PhysicsWorld component to the scene root',
            default = False)

    Scene.urho_navigation = BoolProperty(
            name = 'NavigationMesh',
            description = 'Add NavigationMesh component to the scene root',
            default = False)

    Scene.urho_navigable = BoolProperty(
            name = 'Navigable',
            description = 'Add Navigable component to the scene root',
            default = False)

    Scene.urho_skyboxPath = StringProperty(
            name = 'Skybox',
            description = "Path to xml material file. Note: path is relative to Urho3D 'data' folder.",
            default = 'Materials/Skybox.xml',
            subtype = 'FILE_PATH')

    # Draw Panel layout
    def draw_header(self, blender_context):
        layout = self.layout
        layout.label(icon='WORLD')

    def draw(self, context):
        scn = context.scene
        layout = self.layout
        box = layout.box()

        row = box.row()
        row.prop(scn, 'urho_orientation')
#        row.label('', icon = 'MOD_LATTICE')

        row = box.row()
        row.prop(scn, 'urho_scale')
#        row.label('', icon = 'MOD_LATTICE')

        row = box.row()
        row.prop(scn, 'urho_physics')
        row.label('', icon = 'PHYSICS')

        row = box.row()
        row.prop(scn, 'urho_navigation')
        row.label('', icon = 'MOD_LATTICE')

        if scn.urho_navigation:
            row = box.row()
            row.separator()
            row.prop(scn, 'urho_navigable')
            #row.label('', icon = 'MOD_LATTICE')

        row = box.row()
        row.prop(scn, 'urho_skyboxPath')
        row.label('', icon = 'IMAGE_COL')

        row = layout.row()
        row.operator('urho_scene_prefab.export', text = 'Export', icon = 'EXPORT')


###########
#    Registration
###########
def register():
    bpy.utils.register_module(__name__)

def unregister():
    bpy.utils.unregister_module(__name__)

if __name__ == '__main__':
    register()
