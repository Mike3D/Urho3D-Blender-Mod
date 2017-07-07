
#
# This script is licensed as public domain.
#

from .utils import PathType, GetFilepath, CheckFilepath, \
                   FloatToString, Vector3ToString, Vector4ToString, \
                   WriteXmlFile

from xml.etree import ElementTree as ET
import bpy
import os
import logging

log = logging.getLogger("ExportLogger")

#-------------------------
# Scene and nodes classes
#-------------------------

class UrhoSceneMaterial:
    def __init__(self):
        # Material name
        self.name = None
        # List\Tuple of textures
        self.texturesList = None

    def Load(self, uExportData, uGeometry):
        self.name = uGeometry.uMaterialName
        for uMaterial in uExportData.materials:
            if uMaterial.name == self.name:
                self.texturesList = uMaterial.getTextures()
                break


class UrhoSceneModel:
    def __init__(self):
        # Model name
        self.name = None
        # Blender object name
        self.objectName = None
        # Parent Blender object name
        self.parentObjectName = None
        # Model type
        self.type = None
        # List of UrhoSceneMaterial
        self.materialsList = []
        # Model bounding box
        self.boundingBox = None
        # Model matrix (will be used for instances prefabs)
        self.matrix = None

    def Load(self, uExportData, uModel, objectName):
        self.name = uModel.name

        self.blenderObjectName = objectName
        if objectName:
            parentObject = bpy.data.objects[objectName].parent
            if parentObject and parentObject.type == 'MESH':
                self.parentObjectName = parentObject.name

        if len(uModel.bones) > 0 or len(uModel.morphs) > 0:
            self.type = "AnimatedModel"
        else:
            self.type = "StaticModel"

        for uGeometry in uModel.geometries:
            uSceneMaterial = UrhoSceneMaterial()
            uSceneMaterial.Load(uExportData, uGeometry)
            self.materialsList.append(uSceneMaterial)

        self.boundingBox = uModel.boundingBox


# Hierarchical sorting (based on a post by Hyperboreus at SO)
class Node:
    def __init__(self, name):
        self.name = name
        self.children = []
        self.parent = None

    def to_list(self):
        names = [self.name]
        for child in self.children:
            names.extend(child.to_list())
        return names

class Tree:
    def __init__(self):
    	self.nodes = {}

    def push(self, item):
        name, parent = item
        if name not in self.nodes:
        	self.nodes[name] = Node(name)
        if parent:
            if parent not in self.nodes:
                self.nodes[parent] = Node(parent)
            if parent != name:
                self.nodes[name].parent = self.nodes[parent]
                self.nodes[parent].children.append(self.nodes[name])

    def to_list(self):
        names = []
        for node in self.nodes.values():
            if not node.parent:
                names.extend(node.to_list())
        return names

class UrhoScene:
    def __init__(self, blenderScene):
        # Blender scene name
        self.blenderSceneName = blenderScene.name
        # List of UrhoSceneModel
        self.modelsList = []
        # List of all files
        self.files = {}

    # name must be unique in its type
    def AddFile(self, pathType, name, fileUrhoPath):
        if not name:
            log.critical("Name null type:{:s} path:{:s}".format(pathType, fileUrhoPath) )
            return False
        if name in self.files:
            log.critical("Already added type:{:s} name:{:s}".format(pathType, name) )
            return False
        self.files[pathType+name] = fileUrhoPath
        return True

    def FindFile(self, pathType, name):
        if name is None:
            return None
        try:
            return self.files[pathType+name]
        except KeyError:
            return None

    def Load(self, uExportData, objectName):
        for uModel in uExportData.models:
            uSceneModel = UrhoSceneModel()
            uSceneModel.Load(uExportData, uModel, objectName)
            self.modelsList.append(uSceneModel)

    def SortModels(self):
        # Sort by parent-child relation
        names_tree = Tree()
        for model in self.modelsList:
            ##names_tree.push((model.objectName, model.parentObjectName))
            names_tree.push((model.name, model.parentObjectName))
        # Rearrange the model list in the new order
        orderedModelsList = []
        for name in names_tree.to_list():
            for model in self.modelsList:
                ##if model.objectName == name:
                if model.name == name:
                    orderedModelsList.append(model)
                    # No need to reverse the list, we break straightway
                    self.modelsList.remove(model)
                    break
        self.modelsList = orderedModelsList

#------------------------
# Export materials
#------------------------

def UrhoWriteMaterial(uScene, uMaterial, filepath, fOptions):

    materialElem = ET.Element('material')

    #comment = ET.Comment("Material {:s} created from Blender".format(uMaterial.name))
    #materialElem.append(comment)

    # Technique
    techniquFile = GetFilepath(PathType.TECHNIQUES, uMaterial.techniqueName, fOptions)
    techniqueElem = ET.SubElement(materialElem, "technique")
    techniqueElem.set("name", techniquFile[1])

    # Textures
    if uMaterial.diffuseTexName:
        diffuseElem = ET.SubElement(materialElem, "texture")
        diffuseElem.set("unit", "diffuse")
        diffuseElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.diffuseTexName))

    if uMaterial.normalTexName:
        normalElem = ET.SubElement(materialElem, "texture")
        normalElem.set("unit", "normal")
        normalElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.normalTexName))

    if uMaterial.specularTexName:
        specularElem = ET.SubElement(materialElem, "texture")
        specularElem.set("unit", "specular")
        specularElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.specularTexName))

    if uMaterial.emissiveTexName:
        emissiveElem = ET.SubElement(materialElem, "texture")
        emissiveElem.set("unit", "emissive")
        emissiveElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.emissiveTexName))

    # PS defines
    if uMaterial.psdefines != "":
        psdefineElem = ET.SubElement(materialElem, "shader")
        psdefineElem.set("psdefines", uMaterial.psdefines.lstrip())

    # VS defines
    if uMaterial.vsdefines != "":
        vsdefineElem = ET.SubElement(materialElem, "shader")
        vsdefineElem.set("vsdefines", uMaterial.vsdefines.lstrip())

    # Parameters
    if uMaterial.diffuseColor:
        diffuseColorElem = ET.SubElement(materialElem, "parameter")
        diffuseColorElem.set("name", "MatDiffColor")
        diffuseColorElem.set("value", Vector4ToString(uMaterial.diffuseColor) )

    if uMaterial.specularColor:
        specularElem = ET.SubElement(materialElem, "parameter")
        specularElem.set("name", "MatSpecColor")
        specularElem.set("value", Vector4ToString(uMaterial.specularColor) )

    if uMaterial.emissiveColor:
        emissiveElem = ET.SubElement(materialElem, "parameter")
        emissiveElem.set("name", "MatEmissiveColor")
        emissiveElem.set("value", Vector3ToString(uMaterial.emissiveColor) )

    if uMaterial.twoSided:
        cullElem = ET.SubElement(materialElem, "cull")
        cullElem.set("value", "none")
        shadowCullElem = ET.SubElement(materialElem, "shadowcull")
        shadowCullElem.set("value", "none")

    WriteXmlFile(materialElem, filepath, fOptions)


def UrhoWriteMaterialsList(uScene, uModel, filepath):

    # Search for the model in the UrhoScene
    for uSceneModel in uScene.modelsList:
        if uSceneModel.name == uModel.name:
            break
    else:
        return

    # Get the model materials and their corresponding file paths
    content = ""
    for uSceneMaterial in uSceneModel.materialsList:
        file = uScene.FindFile(PathType.MATERIALS, uSceneMaterial.name)
        # If the file is missing add a placeholder to preserve the order
        if not file:
            file = "null"
        content += file + "\n"

    try:
        file = open(filepath, "w")
    except Exception as e:
        log.error("Cannot open file {:s} {:s}".format(filepath, e))
        return
    file.write(content)
    file.close()
