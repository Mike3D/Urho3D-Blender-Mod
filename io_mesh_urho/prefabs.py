
#
# This script is licensed as public domain.
#

from .utils import CheckFilepath, FloatToString, GetFilepath, PathType, Vector3ToString, WriteXmlFile

import bpy
from bpy_extras.io_utils import axis_conversion
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import Object, Operator, Panel
import logging
from math import degrees, radians
from mathutils import Euler, Matrix, Quaternion, Vector
from xml.etree import ElementTree as ET


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


#------------------------
# Export scene and nodes
#------------------------

# Options for scene and nodes export
class SOptions:
    def __init__(self):
        self.doIndividualPrefab = False
        self.doCollectivePrefab = False
        self.doScenePrefab = False
        self.doPhysics = False
        self.mergeObjects = False
        self.batchComponents = False
        self.navigation = False
        self.createSkybox = False
        self.skyboxPath = ''
        self.exportMode = 'Everything'


def UrhoExportPrefabs(context, uScene, sOptions, fOptions, tOptions, currentPass):

    blenderScene = context.scene

    elements = {} # Map node xml element to model name
    nodeID = 0x1000000  # node ID
    compoID = nodeID    # component ID

    # Create scene components
    if sOptions.doScenePrefab:
        sceneRoot = ET.Element('scene')
        sceneRoot.set('id', '1')

        octreeElem = ET.SubElement(sceneRoot, 'component')
        octreeElem.set('type', 'Octree')
        octreeElem.set('id', '1')

        debugElem = ET.SubElement(sceneRoot, 'component')
        debugElem.set('type', 'DebugRenderer')
        debugElem.set('id', '2')

        # Skybox
        if sOptions.createSkybox:
            skyboxElem = ET.SubElement(sceneRoot, 'component')
            skyboxElem.set('type', 'Skybox')
            skyboxElem.set('id', '4')

            skyboxModelElem = ET.SubElement(skyboxElem, 'attribute')
            skyboxModelElem.set('name', 'Model')
            skyboxModelElem.set('value', 'Model;Models/Box.mdl')

            skyPath = sOptions.skyboxPath
            skyboxMaterialElem = ET.SubElement(skyboxElem, 'attribute')
            skyboxMaterialElem.set('name', 'Material')
            skyboxMaterialElem.set('value', 'Material;{:s}'.format(skyPath))

        if sOptions.navigation:
            navigationElem = ET.SubElement(sceneRoot, 'component')
            navigationElem.set('type', 'NavigationMesh')
            navigationElem.set('id', '5')

        if sOptions.doPhysics and sOptions.exportMode != 'Props': # and !sOptions.exportMode == 'Anims': ADDED exportMode ==========================
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
    else:
        # Root node
        root = ET.Element('node')

    root.set('id', '{:d}'.format(nodeID))
    rootElem = ET.SubElement(root, 'attribute')
    rootElem.set('name', 'Name')
    rootElem.set('value', uScene.blenderSceneName)

    # Set Root node as Navigable
    if sOptions.navigation:
        navigableElem = ET.SubElement(root, 'component')
        navigableElem.set('type', 'Navigable')

    # Sort the models by parent-child relationship
    uScene.SortModels()

    # Export each decomposed object
    for uSceneModel in uScene.modelsList:

        modelNode = uSceneModel.name

        if sOptions.mergeObjects or sOptions.batchComponents: # When merging or batching, get components settings from the active object
            obj = bpy.context.scene.objects.active
        else: obj = bpy.data.objects[modelNode]

        # Get model file relative path
        modelFile = uScene.FindFile(PathType.MODELS, uSceneModel.name)
        if not modelFile:
            log.warning('Prefab generation failure: {:s}.mdl does not exist. You should remove this object from selection (probably 0 face geometry).'.format(uSceneModel.name))
            return

        # Gather materials
        materials = ''
        for uSceneMaterial in uSceneModel.materialsList:
            file = uScene.FindFile(PathType.MATERIALS, uSceneMaterial.name)
            if file is None:
                file = ''
            materials += ';' + file

        if currentPass == 0:
            individualNode = ET.Element('node')
            individualNode.set('id', '{:d}'.format(nodeID))
            nodeID = 0x1000000 # Reset
        else:
            if uSceneModel.type == 'StaticModel' and uSceneModel.parentObjectName and (uSceneModel.parentObjectName in elements): # If child node, parent to parent object instead of root
                for usm in uScene.modelsList:
                    if usm.name == uSceneModel.parentObjectName:
                        elements[modelNode] = ET.SubElement(elements[usm.name], 'node')
                        break
            else:
                elements[modelNode] = ET.SubElement(root, 'node')
            nodeID += 1
            elements[modelNode].set('id', '{:d}'.format(nodeID))

        if currentPass == 0:
            nodeNameElem = ET.SubElement(individualNode, 'attribute')
        else: nodeNameElem = ET.SubElement(elements[modelNode], 'attribute')
        nodeNameElem.set('name', 'Name')
        nodeNameElem.set('value', uSceneModel.name)

        # Add position, rotation and scale when exporting with local origin
        if not tOptions.globalOrigin and currentPass > 0:

            posMatrix = Matrix.Identity(4)
            modelMatrix = obj.matrix_local if (obj.parent and obj.parent.type != 'ARMATURE') else obj.matrix_world

            # Apply custom scene rotation
            front_view = blenderScene.urho_exportsettings.orientation
            if front_view == 'X_PLUS': # Right (+X +Z)
                posMatrix = axis_conversion(to_forward = 'X', to_up = 'Z').to_4x4()
            elif front_view == 'X_MINUS': # Left (-X +Z)
                posMatrix = axis_conversion(to_forward = '-X', to_up = 'Z').to_4x4()
            elif front_view == 'Y_PLUS': # Back (+Y +Z)
                posMatrix = axis_conversion(to_forward = 'Y', to_up = 'Z').to_4x4()
            elif front_view == 'Y_MINUS': # Front (-Y +Z)
                posMatrix = axis_conversion(to_forward = '-Y', to_up = 'Z').to_4x4()
            elif front_view == 'Z_PLUS': # Top (+Z +Y)
                posMatrix = axis_conversion(to_forward = 'Z', to_up = 'Y').to_4x4()
            elif front_view == 'Z_MINUS': # Bottom (-Z -Y)
                posMatrix = axis_conversion(to_forward = '-Z', to_up = '-Y').to_4x4()

            # Apply custom scene scaling last
            if tOptions.scale != 1.0:
                posMatrix *= Matrix.Scale(tOptions.scale, 4)

            nodePos = modelMatrix.to_translation() * posMatrix
            nodePosElem = ET.SubElement(elements[modelNode], 'attribute')
            nodePosElem.set('name', 'Position')
            nodePosElem.set('value', '{:g} {:g} {:g}'.format(nodePos[0], nodePos[2], nodePos[1]))

            nodeRot = (tOptions.orientation.to_matrix().to_4x4() * modelMatrix * posMatrix).to_euler() if tOptions.orientation else (modelMatrix * posMatrix).to_euler()
            nodeRotElem = ET.SubElement(elements[modelNode], 'attribute')
            nodeRotElem.set('name', 'Rotation')
            nodeRotElem.set('value', '{:g} {:g} {:g}'.format(degrees(-nodeRot[0]), degrees(-nodeRot[2]), degrees(-nodeRot[1])))

            nodeScale = (modelMatrix* tOptions.orientation.to_matrix().to_4x4()).to_scale() if tOptions.orientation else modelMatrix.to_scale()
            nodeScaleElem = ET.SubElement(elements[modelNode], 'attribute')
            nodeScaleElem.set('name', 'Scale')
            nodeScaleElem.set('value', '{:g} {:g} {:g}'.format(nodeScale[0], nodeScale[2], nodeScale[1]))

        # Physics
        if sOptions.doPhysics and obj.urho_activate_physics and sOptions.exportMode != 'Props': # and !sOptions.exportMode == 'Anims': ADDED exportMode ==========================

            shapeType = obj.urho_shape_type

            # CollisionShape size
            if obj.urho_overwrite_size:
                 shapeSize = obj.urho_size
            else:
                #Use model's bounding box to compute CollisionShape's size and offset
                bbox = uSceneModel.boundingBox
                x = bbox.max[0] - bbox.min[0]
                y = bbox.max[1] - bbox.min[1]
                z = bbox.max[2] - bbox.min[2]
                size_factor = obj.urho_size_factor
                shapeSize = Vector((x * size_factor[0], y * size_factor[1], z * size_factor[2]))
                if shapeType == 'Box' and y == 0: shapeSize.y = 1 # When Box shape is applied to a plane, set height to 1 instead of 0

            # CollisionShape Offset position
            if obj.urho_overwrite_offset_position:
                shapeOffset = obj.urho_offset_position
            else:
                offsetX = bbox.max[0] - x / 2
                offsetY = bbox.max[1] - y / 2
                offsetZ = bbox.max[2] - z / 2
                shapeOffset = Vector((offsetX, offsetY, offsetZ))
                if shapeType == 'Box' and y == 0: shapeOffset.y = -0.5 # When Box shape is applied to a plane, adjust height

            # RigidBody
            if currentPass == 0:
                bodyElem = ET.SubElement(individualNode, 'component')
                compoID = 0x1000000 # Reset
            else: bodyElem = ET.SubElement(elements[modelNode], 'component')
            bodyElem.set('type', 'RigidBody')
            bodyElem.set('id', '{:d}'.format(compoID))
            compoID += 1

            if obj.urho_mass != 0.0:
                massElem = ET.SubElement(bodyElem, 'attribute')
                massElem.set('name', 'Mass')
                massElem.set('value', FloatToString(obj.urho_mass))

            if obj.urho_friction != 0.5:
                frictionElem = ET.SubElement(bodyElem, 'attribute')
                frictionElem.set('name', 'Friction')
                frictionElem.set('value', FloatToString(obj.urho_friction))

            if Vector3ToString(obj.urho_anisotropic_friction) != '1 1 1':
                anisotropicFrictionElem = ET.SubElement(bodyElem, 'attribute')
                anisotropicFrictionElem.set('name', 'Anisotropic Friction')
                anisotropicFrictionElem.set('value', Vector3ToString(obj.urho_anisotropic_friction))

            if obj.urho_rolling_friction != 0.0:
                rollingFrictionElem = ET.SubElement(bodyElem, 'attribute')
                rollingFrictionElem.set('name', 'Rolling Friction')
                rollingFrictionElem.set('value', FloatToString(obj.urho_rolling_friction))

            if obj.urho_restitution != 0.0:
                restitutionElem = ET.SubElement(bodyElem, 'attribute')
                restitutionElem.set('name', 'Restitution')
                restitutionElem.set('value', FloatToString(obj.urho_restitution))

            if Vector3ToString(obj.urho_linear_velocity) != '0 0 0':
                linearVelocityElem = ET.SubElement(bodyElem, 'attribute')
                linearVelocityElem.set('name', 'Linear Velocity')
                linearVelocityElem.set('value', Vector3ToString(obj.urho_linear_velocity))

            if Vector3ToString(obj.urho_angular_velocity) != '0 0 0':
                angularVelocityElem = ET.SubElement(bodyElem, 'attribute')
                angularVelocityElem.set('name', 'Angular Velocity')
                angularVelocityElem.set('value', Vector3ToString(obj.urho_angular_velocity))

            if Vector3ToString(obj.urho_linear_factor) != '1 1 1':
                linearFactorElem = ET.SubElement(bodyElem, 'attribute')
                linearFactorElem.set('name', 'Linear Factor')
                linearFactorElem.set('value', Vector3ToString(obj.urho_linear_factor))

            if Vector3ToString(obj.urho_angular_factor) != '1 1 1':
                angularFactorElem = ET.SubElement(bodyElem, 'attribute')
                angularFactorElem.set('name', 'Angular Factor')
                angularFactorElem.set('value', Vector3ToString(obj.urho_angular_factor))

            if obj.urho_linear_damping != 0.0:
                linearDampingElem = ET.SubElement(bodyElem, 'attribute')
                linearDampingElem.set('name', 'Linear Damping')
                linearDampingElem.set('value', FloatToString(obj.urho_linear_damping))

            if obj.urho_angular_damping != 0.0:
                angularDampingElem = ET.SubElement(bodyElem, 'attribute')
                angularDampingElem.set('name', 'Angular Damping')
                angularDampingElem.set('value', FloatToString(obj.urho_angular_damping))

            if FloatToString(obj.urho_linear_rest_threshold) != '0.8':
                linearRestThresholdElem = ET.SubElement(bodyElem, 'attribute')
                linearRestThresholdElem.set('name', 'Linear Rest Threshold')
                linearRestThresholdElem.set('value', FloatToString(obj.urho_linear_rest_threshold))

            if obj.urho_angular_rest_threshold != 1.0:
                angularRestThresholdElem = ET.SubElement(bodyElem, 'attribute')
                angularRestThresholdElem.set('name', 'Angular Rest Threshold')
                angularRestThresholdElem.set('value', FloatToString(obj.urho_angular_rest_threshold))

            if obj.urho_collision_layer != [True, False, False, False, False, False, False, False]:
                collisionLayerElem = ET.SubElement(bodyElem, 'attribute')
                collisionLayerElem.set('name', 'Collision Layer')
                collisionLayerElem.set('value', '{:d}'.format(GetBitMask(obj.urho_collision_layer)))

            if obj.urho_collision_mask != [True] * 8:
                collisionMaskElem = ET.SubElement(bodyElem, 'attribute')
                collisionMaskElem.set('name', 'Collision Mask')
                collisionMaskElem.set('value', '{:d}'.format(GetBitMask(obj.urho_collision_mask)))

            if obj.urho_contact_threshold < 1e+17:
                contactThresholdElem = ET.SubElement(bodyElem, 'attribute')
                contactThresholdElem.set('name', 'Contact Threshold')
                contactThresholdElem.set('value', FloatToString(obj.urho_contact_threshold))

            if obj.urho_ccd_radius != 0.0:
                ccdRadiusElem = ET.SubElement(bodyElem, 'attribute')
                ccdRadiusElem.set('name', 'CCD Radius')
                ccdRadiusElem.set('value', FloatToString(obj.urho_ccd_radius))

            if obj.urho_ccd_motion_threshold != 0.0:
                ccdMotionThresholdElem = ET.SubElement(bodyElem, 'attribute')
                ccdMotionThresholdElem.set('name', 'CCD Motion Threshold')
                ccdMotionThresholdElem.set('value', FloatToString(obj.urho_ccd_motion_threshold))

            if obj.urho_collision_event_mode != 'WHENACTIVE':
                ccdMotionThresholdElem = ET.SubElement(bodyElem, 'attribute')
                ccdMotionThresholdElem.set('name', 'CCD Motion Threshold')
                ccdMotionThresholdElem.set('value', obj.urho_collision_event_mode)

            if not obj.urho_use_gravity:
                useGravityElem = ET.SubElement(bodyElem, 'attribute')
                useGravityElem.set('name', 'Use Gravity')
                useGravityElem.set('value', 'false')

            if obj.urho_is_kinematic:
                isKinematicElem = ET.SubElement(bodyElem, 'attribute')
                isKinematicElem.set('name', 'Is Kinematic')
                isKinematicElem.set('value', 'true')

            if obj.urho_is_trigger:
                isTriggerElem = ET.SubElement(bodyElem, 'attribute')
                isTriggerElem.set('name', 'Is Trigger')
                isTriggerElem.set('value', 'true')

            if Vector3ToString(obj.urho_gravity_override) != '0 0 0':
                gravityOverrideElem = ET.SubElement(bodyElem, 'attribute')
                gravityOverrideElem.set('name', 'Gravity Override')
                gravityOverrideElem.set('value', Vector3ToString(obj.urho_gravity_override))

            # CollisionShape
            if currentPass == 0: shapeElem = ET.SubElement(individualNode, 'component')
            else: shapeElem = ET.SubElement(elements[modelNode], 'component')
            shapeElem.set('type', 'CollisionShape')
            shapeElem.set('id', '{:d}'.format(compoID))
            compoID += 1

            shapeTypeElem = ET.SubElement(shapeElem, 'attribute')
            shapeTypeElem.set('name', 'Shape Type')
            shapeTypeElem.set('value', shapeType)

            if shapeType == 'TriangleMesh' or shapeType == 'ConvexHull':
                if obj.urho_overwrite_model:
                    triangleMesh = obj.urho_model
                else: triangleMesh = modelFile
                modelElem = ET.SubElement(shapeElem, 'attribute')
                modelElem.set('name', 'Model')
                modelElem.set('value', 'Model;' + triangleMesh)

            else:
                if Vector3ToString(shapeSize) != '1 1 1':
                    sizeElem = ET.SubElement(shapeElem, 'attribute')
                    sizeElem.set('name', 'Size')
                    sizeElem.set('value', Vector3ToString(shapeSize))

                if Vector3ToString(shapeOffset) != '0 0 0':
                    offsetPositionElem = ET.SubElement(shapeElem, 'attribute')
                    offsetPositionElem.set('name', 'Offset Position')
                    offsetPositionElem.set('value', Vector3ToString(shapeOffset))

            if Vector3ToString(obj.urho_offset_rotation) != '0 0 0':
                offsetRotationElem = ET.SubElement(shapeElem, 'attribute')
                offsetRotationElem.set('name', 'Offset Rotation')
                offsetRotationElem.set('value', Vector3ToString(obj.urho_offset_rotation))

            if obj.urho_lod_level != 0:
                lodLevelElem = ET.SubElement(shapeElem, 'attribute')
                lodLevelElem.set('name', 'LOD Level')
                lodLevelElem.set('value', '{:d}'.format(obj.urho_lod_level))

            if FloatToString(obj.urho_collision_margin) != '0.04':
                collisionMarginElem = ET.SubElement(shapeElem, 'attribute')
                collisionMarginElem.set('name', 'Collision Margin')
                collisionMarginElem.set('value', FloatToString(obj.urho_collision_margin))

            if obj.urho_customgeometry_nodeid != 0:
                customGeometryNodeIDElem = ET.SubElement(shapeElem, 'attribute')
                customGeometryNodeIDElem.set('name', 'CustomGeometry NodeID')
                customGeometryNodeIDElem.set('value', '{:d}'.format(obj.urho_customgeometry_nodeid))

        # Sub-node
        if obj.urho_create_subnode and currentPass == 0:
            subNodeElem = ET.SubElement(individualNode, 'node')
            subNodeElem.set('id', '{:d}'.format(nodeID))

            subNodeNameElem = ET.SubElement(subNodeElem, 'attribute')
            subNodeNameElem.set('name', 'Name')
            subNodeNameElem.set('value', 'SubNode')

            if Vector3ToString(obj.urho_subnode_rotation) != '0 0 0':
                subNodeRotationElem = ET.SubElement(subNodeElem, 'attribute')
                subNodeRotationElem.set('name', 'Rotation')
                subNodeRotationElem.set('value', Vector3ToString(obj.urho_subnode_rotation))

        # Navigable
        if obj.urho_create_navigable and (uSceneModel.type == 'StaticModel' or uSceneModel.type == 'TerrainPatch'):
            if currentPass == 0:
                if obj.urho_create_subnode: parent = subNodeElem
                else: parent = individualNode
                typeElem = ET.SubElement(parent, 'component')
            else: typeElem = ET.SubElement(elements[modelNode], 'component')
            typeElem.set('type', 'Navigable')
            typeElem.set('id', '{:d}'.format(compoID))
            compoID += 1

        # StaticModel / AnimatedModel
        if currentPass == 0:
            if obj.urho_create_subnode: parent = subNodeElem
            else: parent = individualNode
            typeElem = ET.SubElement(parent, 'component')
        else: typeElem = ET.SubElement(elements[modelNode], 'component')
        typeElem.set('type', uSceneModel.type)
        typeElem.set('id', '{:d}'.format(compoID))
        compoID += 1

        nodeModelElem = ET.SubElement(typeElem, 'attribute')
        nodeModelElem.set('name', 'Model')
        nodeModelElem.set('value', 'Model;' + modelFile)

        nodeMaterialElem = ET.SubElement(typeElem, 'attribute')
        nodeMaterialElem.set('name', 'Material')
        nodeMaterialElem.set('value', 'Material' + materials)

        if obj.urho_is_occluder:
            isOccluderElem = ET.SubElement(typeElem, 'attribute')
            isOccluderElem.set('name', 'Is Occluder')
            isOccluderElem.set('value', 'true')

        if not obj.urho_can_be_occluded:
            canBeOccludedElem = ET.SubElement(typeElem, 'attribute')
            canBeOccludedElem.set('name', 'Can Be Occluded')
            canBeOccludedElem.set('value', 'false')

        if obj.urho_cast_shadows:
            castShadowsElem = ET.SubElement(typeElem, 'attribute')
            castShadowsElem.set('name', 'Cast Shadows')
            castShadowsElem.set('value', 'true')

        if obj.urho_draw_distance != 0.0:
            drawDistanceElem = ET.SubElement(typeElem, 'attribute')
            drawDistanceElem.set('name', 'Draw Distance')
            drawDistanceElem.set('value', FloatToString(obj.urho_draw_distance))

        if obj.urho_shadow_distance != 0.0:
            shadowDistanceElem = ET.SubElement(typeElem, 'attribute')
            shadowDistanceElem.set('name', 'Shadow Distance')
            shadowDistanceElem.set('value', FloatToString(obj.urho_shadow_distance))

        if obj.urho_lod_bias != 1.0:
            lodBiasElem = ET.SubElement(typeElem, 'attribute')
            lodBiasElem.set('name', 'LOD Bias')
            lodBiasElem.set('value', FloatToString(obj.urho_lod_bias))

        if obj.urho_max_lights != 0:
            maxLightsElem = ET.SubElement(typeElem, 'attribute')
            maxLightsElem.set('name', 'Max Lights')
            maxLightsElem.set('value', '{:d}'.format(obj.urho_max_lights))

        if obj.urho_view_mask != [True] * 8:
            viewMaskElem = ET.SubElement(typeElem, 'attribute')
            viewMaskElem.set('name', 'View Mask')
            viewMaskElem.set('value', '{:d}'.format(GetBitMask(obj.urho_view_mask)))

        if obj.urho_light_mask != [True] * 8:
            lightMaskElem = ET.SubElement(typeElem, 'attribute')
            lightMaskElem.set('name', 'Light Mask')
            lightMaskElem.set('value', '{:d}'.format(GetBitMask(obj.urho_light_mask)))

        if obj.urho_shadow_mask != [True] * 8:
            shadowMaskElem = ET.SubElement(typeElem, 'attribute')
            shadowMaskElem.set('name', 'Shadow Mask')
            shadowMaskElem.set('value', '{:d}'.format(GetBitMask(obj.urho_shadow_mask)))

        if obj.urho_zone_mask != [True] * 8:
            zoneMaskElem = ET.SubElement(typeElem, 'attribute')
            zoneMaskElem.set('name', 'Zone Mask')
            zoneMaskElem.set('value', '{:d}'.format(GetBitMask(obj.urho_zone_mask)))

        if obj.urho_occlusion_lod_level != -1:
            occlusionLodLevelElem = ET.SubElement(typeElem, 'attribute')
            occlusionLodLevelElem.set('name', 'Occlusion LOD Level')
            occlusionLodLevelElem.set('value', '{:d}'.format(obj.urho_occlusion_lod_level))


        # Write individual prefabs
        if currentPass == 0:
            xml = individualNode
            filepath = GetFilepath(PathType.OBJECTS, uSceneModel.name, fOptions)
            if CheckFilepath(filepath[0], fOptions):
                log.info( 'Creating individual prefab {:s}'.format(filepath[1]) )
                WriteXmlFile(xml, filepath[0], fOptions)

    # Write collective and scene prefab files
    if sOptions.doCollectivePrefab and not sOptions.mergeObjects:
        filepath = GetFilepath(PathType.OBJECTS, uScene.blenderSceneName, fOptions)
        if CheckFilepath(filepath[0], fOptions):
            log.info( 'Creating collective prefab {:s}'.format(filepath[1]) )
            WriteXmlFile(root, filepath[0], fOptions)

    if sOptions.doScenePrefab:
        filepath = GetFilepath(PathType.SCENES, uScene.blenderSceneName, fOptions)
        if CheckFilepath(filepath[0], fOptions):
            log.info( 'Creating scene prefab {:s}'.format(filepath[1]) )
            WriteXmlFile(sceneRoot, filepath[0], fOptions)


#-------------------------
# Object custom panel
#-------------------------

# Reset settings buttons

# AnimatedModel / StaticModel
class UrhoComponentModelResetOperator(Operator):
    bl_idname      = 'urho.modelreset'
    bl_label           = 'Reset'
    bl_description = 'Restore defaults'

    def execute(self, context):
        obj = context.object
        obj.urho_is_occluder = False
        obj.urho_can_be_occluded = True
        obj.urho_cast_shadows = False
        obj.urho_draw_distance = 0.0
        obj.urho_shadow_distance = 0.0
        obj.urho_lod_bias = 1.0
        obj.urho_max_lights = 0
        obj.urho_view_mask = [True] * 8
        obj.urho_light_mask = [True] * 8
        obj.urho_shadow_mask = [True] * 8
        obj.urho_zone_mask = [True] * 8
        obj.urho_occlusion_lod_level = -1
        return {'FINISHED'}

# CollisionShape
class UrhoComponentShapeResetOperator(Operator):
    bl_idname      = 'urho.shapereset'
    bl_label           = 'Reset'
    bl_description = 'Restore defaults'

    def execute(self, context):
        obj = context.object
        obj.urho_shape_type = 'TriangleMesh'
        obj.urho_size_factor = default = (1.0, 1.0, 1.0)
        obj.urho_offset_rotation = (0.0, 0.0, 0.0)
        obj.urho_lod_level = 0
        obj.urho_collision_margin = 0.04
        obj.urho_customgeometry_nodeid = 0
        obj.urho_overwrite_model = False
        obj.urho_model = ''
        obj.urho_overwrite_size = False
        obj.urho_size = (0.0, 0.0, 0.0)
        obj.urho_overwrite_offset_position = False
        obj.urho_offset_position = (0.0, 0.0, 0.0)
        return {'FINISHED'}

# RigidBody
class UrhoComponentBodyResetOperator(Operator):
    bl_idname      = 'urho.bodyreset'
    bl_label           = 'Reset'
    bl_description = 'Restore defaults'

    def execute(self, context):
        obj = context.object
        obj.urho_mass = 0.0
        obj.urho_friction = 0.5
        obj.urho_anisotropic_friction = (1.0, 1.0, 1.0)
        obj.urho_rolling_friction = 0.0
        obj.urho_restitution = 0.0
        obj.urho_linear_velocity = (0.0, 0.0, 0.0)
        obj.urho_angular_velocity = (0.0, 0.0, 0.0)
        obj.urho_linear_factor = (1.0, 1.0, 1.0)
        obj.urho_angular_factor = (1.0, 1.0, 1.0)
        obj.urho_linear_damping = 0.0
        obj.urho_angular_damping = 0.0
        obj.urho_linear_rest_threshold = 0.8
        obj.urho_angular_rest_threshold = 1.0
        obj.urho_collision_layer = [True, False, False, False, False, False, False, False]
        obj.urho_collision_mask = [True] * 8
        obj.urho_contact_threshold = 1e+18
        obj.urho_ccd_radius = 0.0
        obj.urho_ccd_motion_threshold = 0.0
        obj.urho_collision_event_mode = 'WHENACTIVE'
        obj.urho_use_gravity = True
        obj.urho_is_kinematic = False
        obj.urho_is_trigger = False
        obj.urho_gravity_override = (0.0, 0.0, 0.0)
        return {'FINISHED'}


#-------------------------
# Properties > Object > 'Urho3D ~ Components' panel
#-------------------------
class UrhoComponentsPanel(Panel):
    bl_idname = 'Urho3Dcomponents'
    bl_label = 'Urho3D ~ Components'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'object'

    Obj = Object

    # StaticModel / AnimatedModel
    Obj.urho_is_occluder = BoolProperty(name = 'Is Occluder', default = False)
    Obj.urho_can_be_occluded = BoolProperty(name = 'Can Be Occluded', default = True)
    Obj.urho_cast_shadows = BoolProperty(name = 'Cast Shadows', default = False)
    Obj.urho_draw_distance = FloatProperty(name = 'Draw Distance', default = 0.0, step = 10, precision = 2)
    Obj.urho_shadow_distance = FloatProperty(name = 'Shadow Distance', default = 0.0)
    Obj.urho_lod_bias = FloatProperty(name = 'LOD Bias', default = 1.0, step = 10, precision = 2)
    Obj.urho_max_lights = IntProperty(name = 'Max Lights', default = 0, min = 0)
    Obj.urho_view_mask = BoolVectorProperty(name = 'View Mask', default = (True, True, True, True, True, True, True, True), subtype = 'LAYER', size = 8)
    Obj.urho_light_mask = BoolVectorProperty(name = 'Light Mask', default = (True, True, True, True, True, True, True, True), subtype = 'LAYER', size = 8)
    Obj.urho_shadow_mask = BoolVectorProperty(name = 'Shadow Mask', default = (True, True, True, True, True, True, True, True), subtype = 'LAYER', size = 8)
    Obj.urho_zone_mask = BoolVectorProperty(name = 'Zone Mask', default = (True, True, True, True, True, True, True, True), subtype = 'LAYER', size = 8)
    Obj.urho_occlusion_lod_level = IntProperty(name = 'Occlusion LOD Level', default = -1)

    # Sub-node
    Obj.urho_create_subnode = BoolProperty(name = 'Create as sub-node', description = "Parent the 'StaticModel' or 'AnimatedModel' to a sub-node to allow independant rotation", default = False)
    Obj.urho_subnode_rotation = FloatVectorProperty(name = 'Rotation', description = 'Sub-node rotation in degrees', default = (0.0, 0.0, 0.0), min = 0.0, step = 10, precision = 1, subtype = 'NONE', size = 3)

    # Navigable
    Obj.urho_create_navigable = BoolProperty(name = 'Create Navigable Component', description = 'Note that only StaticModel & TerrainPatch are supported', default = False)

    # Physics Switch
    Obj.urho_activate_physics = BoolProperty(name = 'Activate Physics', description = 'Create RigidBody and CollisionShape with settings below', default = False)

    # CollisionShape
    shapeItems = [ ('Box', 'Box', ''), ('Capsule', 'Capsule', ''), ('Cone', 'Cone', ''), ('ConvexHull', 'ConvexHull', ''), ('Cylinder', 'Cylinder', ''), ('Sphere', 'Sphere', ''), ('StaticPlane', 'StaticPlane', ''), ('TriangleMesh', 'TriangleMesh', '') ]
    Obj.urho_shape_type = EnumProperty(name = 'Shape Type', items = shapeItems, default = 'TriangleMesh')
    Obj.urho_size_factor = FloatVectorProperty(name = 'Size Factor', description = "Size is computed from bounding box. If need be, apply a corrective factor here or set size yourself in 'Size: overwrite computed' below.", default = (1.0, 1.0, 1.0), min = 0.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_offset_rotation = FloatVectorProperty(name = 'Offset Rotation', default = (0.0, 0.0, 0.0), min = 0.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_lod_level = IntProperty(name = 'LOD Level', default = 0, min = 0)
    Obj.urho_collision_margin = FloatProperty(name = 'Collision Margin', default = 0.04, min = 0.01, step = 10, precision = 2)
    Obj.urho_customgeometry_nodeid = IntProperty(name = 'CustomGeometry NodeID', default = 0, min = 0)
    Obj.urho_overwrite_model = BoolProperty(name = 'Model: overwrite computed', description = 'By default the exported mdl model is used, but you can choose another model below.', default = False) # Overwrite computation based on exported mdl model
    Obj.urho_model = StringProperty(name = "Model', description = 'Path to your mdl file. Note: path is relative to Urho3D 'Data' folder.", default = '', subtype = 'FILE_PATH')
    Obj.urho_overwrite_size = BoolProperty(name = 'Size: overwrite computed', description = 'Size is computed for you at export, but you can set another one below.', default = False) # Overwrite computation based on bounding box
    Obj.urho_size = FloatVectorProperty(name = 'Size', description = 'Leave to ZERO if you do not want to discard computed size', default = (0.0, 0.0, 0.0), min = 0.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_overwrite_offset_position = BoolProperty(name = 'Offset Position: overwrite computed', description = 'Offset Position is computed for you at export, but you can set a different value below.', default = False) # Overwrite computation based on bounding box
    Obj.urho_offset_position = FloatVectorProperty(name = 'Offset Position', description = '', default = (0.0, 0.0, 0.0), min = 0.0, step = 10, precision = 2, subtype = 'NONE', size = 3)

    # RigidBody
    Obj.urho_mass = FloatProperty(name = 'Mass', default = 0.0, min = 0.0, max = 1000.0, step = 10, precision = 2)
    Obj.urho_friction = FloatProperty(name = 'Friction', default = 0.5, min = 0.0, max = 1.0, step = 10, precision = 2)
    Obj.urho_anisotropic_friction = FloatVectorProperty(name = 'Anisotropic Friction', default = (1.0, 1.0, 1.0), min = 0.0, max = 1.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_rolling_friction = FloatProperty(name = 'Rolling Friction', default = 0.0, min = 0.0, max = 1.0, step = 10, precision = 2)
    Obj.urho_restitution = FloatProperty(name = 'Restitution', default = 0.0, min = 0.0, max = 1.0, step = 10, precision = 2)
    Obj.urho_linear_velocity = FloatVectorProperty(name = 'Linear Velocity', default = (0.0, 0.0, 0.0), min = 0.0, max = 1.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_angular_velocity = FloatVectorProperty(name = 'Angular Velocity', default = (0.0, 0.0, 0.0), min = 0.0, max = 1.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_linear_factor = FloatVectorProperty(name = 'Linear Factor', default = (1.0, 1.0, 1.0), min = 0.0, max = 1.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_angular_factor = FloatVectorProperty(name = 'AngularFactor', description = '', default = (1.0, 1.0, 1.0), min = 0.0, max = 1.0, step = 10, precision = 2, subtype = 'NONE', size = 3)
    Obj.urho_linear_damping = FloatProperty(name = 'Linear Damping', default = 0.0, min = 0.0, max = 10.0, step = 10, precision = 2)
    Obj.urho_angular_damping = FloatProperty(name = 'Angular Damping', default = 0.0, min = 0.0, max = 1.0, step = 10, precision = 2)
    Obj.urho_linear_rest_threshold = FloatProperty(name = 'Linear Rest Threshold ', default = 0.8, min = 0.0, max = 1.0, step = 10, precision = 2)
    Obj.urho_angular_rest_threshold = FloatProperty(name = 'Angular Rest Threshold', default = 1.0, min = 0.0, max = 1.0, step = 10, precision = 2)
    Obj.urho_collision_layer = BoolVectorProperty(name = 'CollisionLayer', default = (True, False, False, False, False, False, False, False), subtype = 'LAYER', size = 8)
    Obj.urho_collision_mask = BoolVectorProperty(name = 'Collision Mask', default = (True, True, True, True, True, True, True, True), subtype = 'LAYER', size = 8)
    Obj.urho_contact_threshold = FloatProperty(name = 'Contact Threshold', default = 1e+18, min = 0.0, step = 1000, precision = 0)
    Obj.urho_ccd_radius = FloatProperty(name = 'CCD Radius', default = 0.0, min = 0.0, max = 10.0, step = 10, precision = 2)
    Obj.urho_ccd_motion_threshold = FloatProperty(name = 'CCD Motion Threshold', default = 0.0, min = 0.0, max = 10.0, step = 10, precision = 2)
    Obj.urho_collision_event_mode = EnumProperty(name = 'Collision Event Mode', items = [('WHENACTIVE', 'When Active', ''), ('NEVER', 'Never', ''), ('ALWAYS', 'Always', '')], default = 'WHENACTIVE')
    Obj.urho_use_gravity = BoolProperty(name = 'Use Gravity', default = True)
    Obj.urho_is_kinematic = BoolProperty(name = 'Is Kinematic', default = False)
    Obj.urho_is_trigger = BoolProperty(name = 'Is Trigger', default = False)
    Obj.urho_gravity_override = FloatVectorProperty(name = 'Gravity Override', default = (0.0, 0.0, 0.0), min = 0.0, max = 1.0, step = 10, precision = 2, subtype = 'NONE', size = 3)

    # Draw the panel inside Properties > Object
    def draw_header(self, blender_context):
        layout = self.layout
        layout.label(icon='MOD_OCEAN')

    def draw(self, context):
        layout = self.layout
        obj = context.object
        if not obj: return

        if obj.type != 'MESH': # Draw panel only for mesh objects
            row = layout.row()
            row.label(text = 'Select the mesh object if you want to set its components', icon = 'INFO')
            return

        #StaticModel / AnimatedModel
        row = layout.row()
        row.label(text = 'StaticModel / AnimatedModel', icon = 'FACESEL_HLT')
        row.operator('urho.modelreset', text = '', icon = 'FILE_REFRESH')
        box = layout.box()

        row = box.row()
        row.prop(obj, 'urho_is_occluder')
        row.prop(obj, 'urho_can_be_occluded')
        row.prop(obj, 'urho_cast_shadows')

        row = box.row()
        row.prop(obj, 'urho_draw_distance')
        row.prop(obj, 'urho_shadow_distance')

        row = box.row()
        row.prop(obj, 'urho_lod_bias')
        row.prop(obj, 'urho_max_lights')

        row = box.row()
        row.column().prop(obj, 'urho_view_mask')
        row.column().prop(obj, 'urho_light_mask')

        row = box.row()
        row.column().prop(obj, 'urho_shadow_mask')
        row.column().prop(obj, 'urho_zone_mask')

        row = box.row()
        row.prop(obj, 'urho_occlusion_lod_level')

        # Sub-node
        layout.label(text = 'Sub-node', icon = 'OOPS')
        box = layout.box()

        row = box.row()
        row.prop(obj, 'urho_create_subnode')

        if obj.urho_create_subnode:
            row = box.row()
            row.prop(obj, 'urho_subnode_rotation')

        # Navigable
        layout.label(text = 'Navigable', icon = 'MOD_LATTICE')
        box = layout.box()

        row = box.row()
        row.prop(obj, 'urho_create_navigable')

        # Physics Switch
        layout.label(text = 'Activate Physics', icon = 'PHYSICS')
        box = layout.box()

        row = box.row()
        row.prop(obj, 'urho_activate_physics')

        if not obj.urho_activate_physics:
            return

        #CollisionShape
        row = layout.row()
        row.label(text = 'CollisionShape', icon = 'SURFACE_NCYLINDER')
        row.operator('urho.shapereset', text = '', icon = 'FILE_REFRESH')
        box = layout.box()

        row = box.row()
        row.prop(obj, 'urho_shape_type')

        row = box.row()
        row.prop(obj, 'urho_size_factor')

        row = box.row()
        row.prop(obj, 'urho_offset_rotation')

        row = box.row()
        row.prop(obj, 'urho_lod_level')
        row.prop(obj, 'urho_collision_margin')

        row = box.row()
        row.prop(obj, 'urho_customgeometry_nodeid')

        row = box.row()
        row.prop(obj, 'urho_overwrite_model')
        row.prop(obj, 'urho_overwrite_size')
        row.prop(obj, 'urho_overwrite_offset_position')

        if obj.urho_overwrite_model:
            row = box.row()
            row.prop(obj, 'urho_model')

        if obj.urho_overwrite_size:
            row = box.row()
            row.prop(obj, 'urho_size')

        if obj.urho_overwrite_offset_position:
            row = box.row()
            row.prop(obj, 'urho_offset_position')

        #RigidBody
        row = layout.row()
        row.label(text = 'RigidBody', icon = 'MOD_VERTEX_WEIGHT')
        row.operator('urho.bodyreset', text = '', icon = 'FILE_REFRESH')
        box = layout.box()

        row = box.row()
        row.prop(obj, 'urho_mass')
        row.prop(obj, 'urho_friction')

        row = box.row()
        row.prop(obj, 'urho_anisotropic_friction')

        row = box.row()
        row.prop(obj, 'urho_rolling_friction')
        row.prop(obj, 'urho_restitution')

        row = box.row()
        row.prop(obj, 'urho_linear_velocity')

        row = box.row()
        row.prop(obj, 'urho_angular_velocity')

        row = box.row()
        row.prop(obj, 'urho_linear_factor')

        row = box.row()
        row.prop(obj, 'urho_angular_factor')

        row = box.row()
        row.prop(obj, 'urho_linear_damping')
        row.prop(obj, 'urho_angular_damping')

        row = box.row()
        row.prop(obj, 'urho_linear_rest_threshold')
        row.prop(obj, 'urho_angular_rest_threshold')

        row = box.row()
        row.column().prop(obj, 'urho_collision_layer')
        row.column().prop(obj, 'urho_collision_mask')

        row = box.row()
        row.prop(obj, 'urho_contact_threshold')

        row = box.row()
        row.prop(obj, 'urho_ccd_radius')
        row.prop(obj, 'urho_ccd_motion_threshold')

        row = box.row()
        row.prop(obj, 'urho_collision_event_mode')

        row = box.row()
        row.prop(obj, 'urho_use_gravity')
        row.prop(obj, 'urho_is_kinematic')
        row.prop(obj, 'urho_is_trigger')

        row = box.row()
        row.prop(obj, 'urho_gravity_override')
