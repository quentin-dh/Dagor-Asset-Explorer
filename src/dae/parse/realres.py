import sys
from os import path, getcwd, mkdir, makedirs
from typing import Iterable, Union

sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))


import util.log as log
from io import BytesIO
from util.fileread import *
from util.enums import *
from util.decompression import CompressedData, zlibDecompress
from util.misc import getResPath, vectorTransform, matrixMul, matrixToEuler
from util.assetcacher import AssetCacher
from struct import unpack, pack
from parse.datablock import *
from util.terminable import Packed, SafeRange, SafeIter, SafeEnumerate, Terminable
from parse.mesh import MatVData, InstShaderMeshResource, ShaderMesh
from parse.material import MaterialData, MaterialTemplateLibrary,  computeMaterialNames, TexturePathDict
from abc import abstractmethod, ABC
from math import sqrt


PHYSMAT_TO_SURFACEPROP = {
	"default":"default",
	"horLandMesh":"dirt",
	"verLandMesh":"dirt",
	"water3d":"water",
	"water":"water",
	"concrete":"concrete",
	"bricks_red":"brick",
	"buildings":"concrete",
	"fabric":"???",
	"glass":"glass",
	"glass_small":"glass",
	"metal":"metal",
	"stone_snow":"rock",
	"ships":"metalvehicle",
	"airfield":"dirt",
	"wood":"wood",
	"steel":"metal",
	"aluminum_armor":"metal",
	"tank_structural_steel":"metalvehicle",
	"tank_rubber_screens":"rubber",
	"armorPierceLowCal":"default",
	"armorPierceMedCal":"default",
	"armorPierceHiCal":"default",
	"armorNPLowCal":"default",
	"armorNPMedCal":"default",
	"armorNPHiCal":"default",
	"soil":"dirt",
	"tankTrack":"metalvehicle",
	"wheel":"rubber",
	"road":"asphalt",
	"dirt":"dirt",
	"visibilityRay":"default",
	"roadSoil":"dirt",
	"sand":"sand",
	"duneSand":"sand",
	"roadSand":"sand",
	"quickSand":"sand",
	"snow":"snow",
	"ice":"ice",
	"football_ramp":"default",
	"snowLower":"snow",
	"roadSnow":"asphalt",
	"rocks":"rock",
	"rocksSlippery":"rock",
	"transparentBeton":"concrete",
	"visibilityCamera":"default",
	"transparentCamera":"default"
}

TINY_TRIANGLE_SMD = "\n".join((
	"version 1",
	
	"nodes",
	"0 \"_ROOT\" -1",
	"end",

	"skeleton",
	"time 0",
	"0 0.0000 0.0000 0.0000 -0.0000 0.0000 0.0000",
	"end",
	
	"triangles",
	"tinytriangle",
	"0 0.00 0.00 0.00 0.0 0.0 1.0 1.0 0.0",
	"0 0.01 0.00 0.00 0.0 0.0 1.0 0.0 0.5",
	"0 0.00 0.01 0.00 0.0 0.0 1.0 1.0 0.0",
	"end"
))

UNKNOWN_ICO_PATH = getResPath("unknown.bmp")
REALRES_CLASSES_LIST:list[type] = None
REALRES_CLASSES_DICT:dict[int, type] = None

SOURCE_UNITS_METERS = 1.905

VERTEX_SCALE = 40
MAX_SOURCE_VERTS = 11000
MAX_SOURCE_BONES = 128

DMF_MAGIC = b"DMF\x02"
DMF_VDATA_OFS = 0x10
DMF_MAT_PTR = 0x8
DMF_SKE_PTR = 0xC
DMF_NO_PARENT = -1

def subVert(v1, v2):
	return (v1[0] - v2[0], v1[1] - v2[1], v1[2] - v2[2])

def crossProduct(v1, v2):
	return (v1[1] * v2[2] - v1[2] * v2[1],
			v1[2] * v2[0] - v1[0] * v2[2],
			v1[0] * v2[1] - v1[1] * v2[0])

def getNormal(v1, v2, v3):
	return crossProduct(subVert(v2, v1), subVert(v3, v1))

def normalize(normal):
	length = sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2])
	
	if length != 0.0:
		normal[0] /= length
		normal[1] /= length
		normal[2] /= length

class SkinnedMesh(Terminable):
	skinNodes:tuple[int]
	mesh:tuple[ShaderMesh]

	def computeData(self, file:BinFile, lod):
		lod:DynModel.Lod

		self.skinNodes = tuple(readInt(file) for _ in SafeRange(self, lod.skinCnt))
		self.sizes = tuple(readLong(file) & 0xBFFFFFFF for _ in SafeRange(self, lod.skinCnt))
		self.mesh = tuple(self.computeMesh(file.readBlock(v), lod, k) for k, v in SafeEnumerate(self, self.sizes))
			

	def computeMesh(self, file:BinFile, lod, idx:int):
		log.log(f"Computing mesh {idx}")
		log.addLevel()

		lod:DynModel.Lod
		
		boneOrgTmOfs = readInt(file)
		boneOrgTmCnt = readInt(file)
		
		file.seek(8, 1)

		matTabOfs = readInt(file)
		matTabCnt = readInt(file)
		
		file.seek(8, 1)

		boneNodeIdOfs = readInt(file)
		boneNodeIdCnt = readInt(file)

		shMatOfs = readInt(file)
		shMatCnt = readInt(file)

		workDataOfs = readInt(file)
		workDataCnt = readInt(file)

		shaderMesh = ShaderMesh(file)

		file.seek(matTabOfs, 0)
		matTab = tuple((readLong(file), readLong(file)) for _ in SafeRange(self, matTabCnt))

		file.seek(boneNodeIdOfs, 0)
		boneNodeIds = tuple(readShort(file) for _ in SafeRange(self, boneOrgTmCnt))

		file.seek(boneOrgTmOfs, 0)
		boneOrgTm = tuple((
			unpack("fff", file.read(0xC)),
			unpack("fff", file.read(0xC)),
			unpack("fff", file.read(0xC)),
			unpack("fff", file.read(0xC))
		) for _ in SafeRange(self, boneOrgTmCnt))

		log.subLevel()

		return shaderMesh

class SkinnedMeshResource(Terminable):
	def __init__(self, name:str):
		self.__name = name
	
	@property
	def name(self):
		return self.__name

	def computeData(self, file:BinFile, mvdHdrFlag:bool, textures:list[str], lods:list):
		lods:list[DynModel.Lod]

		ptr = readLong(file)

		texCnt = readInt(file)
		matCnt = readInt(file)

		vdCnt = readInt(file)
		flags = readInt(file)

		log.log(f"ptr		= {ptr}")
		log.log(f"texCnt	= {texCnt}")
		log.log(f"matCnt	= {matCnt}")
		log.log(f"vdCnt 	= {vdCnt}")
		log.log(f"flags 	= {hex(flags)}")

		mvd = MatVData(CompressedData(file).decompressToBin(), 
		 				f"{self.name}_skinned",
						flag = MVD_SKINNED if mvdHdrFlag else MVD_SKINNED_FLAG,
						textures = textures)
		
		self.__mvd = mvd
		# mvd.save()
		mvd.computeData()

		skins = BinFile(zlibDecompress(file.readRest()))

		# f = open(f"{self.name}_skinnedmesh.bin", "wb")
		# f.write(skins.read())
		# f.close()

		self.__skinnedMesh:list[SkinnedMesh] = []

		for idx, lod in SafeEnumerate(self, lods):
			lod:DynModel.Lod

			log.log(f"Loading skinned mesh for LOD {(len(lods) - idx) - 1}")
			log.addLevel()

			sm = SkinnedMesh()

			self.setSubTask(sm)

			sm.computeData(skins, lod)
			self.clearSubTask()

			self.__skinnedMesh.append(sm)

			log.subLevel()
	@property
	def mvd(self):
		return self.__mvd
	
	@property
	def skinnedMesh(self):
		return self.__skinnedMesh



class SMD(Terminable):
	class Triangle:
		def __init__(self, 
					material:str, 
					verts:tuple[tuple, tuple, tuple],
					UVs:tuple[tuple, tuple]):
			self.material = material
			self.verts = verts
			self.normals = ((0, 0, 0), (0, 0, 0), (0, 0, 0))
			self.UVs = UVs
		
		def getNormal(self):
			return self.normal

		def getString(self, boneIdx:int = 0, weight:float = 1):
			return "\n".join((
				self.material,
				*(
					f"{boneIdx} {self.formatTuple(self.verts[i])} {self.formatTuple(self.normals[i])} {self.formatTuple(self.UVs[i])}"# {weight}"
					for i in range(3)
				)
			))
		
		def transform(self, matrix):
			self.verts = (
				vectorTransform(matrix, self.verts[0]),
				vectorTransform(matrix, self.verts[1]),
				vectorTransform(matrix, self.verts[2])
			)
		
		def formatTuple(self, array:tuple[float]):
			return " ".join(tuple(f"{v:.6f}" for v in array))
	
	def __init__(self, name:str, baseName:str):
		self.__name = name
		self.__baseName = baseName
		self.__triangles:list[SMD.Triangle] = []
		self.__boneIdx = 0
		self.__weight = 1
	
	def transform(self, matrix):
		for tri in SafeIter(self, self.__triangles):
			tri:SMD.Triangle

			tri.transform(matrix)
	
	@property
	def baseName(self):
		return self.__baseName
	
	@property
	def name(self):
		return self.__name

	def newTriangle(self,
			material:str, 
			verts:tuple[tuple, tuple, tuple],
			UVs:tuple[tuple, tuple, tuple]):
		triangle = self.Triangle(material, verts, UVs)

		self.__triangles.append(triangle)

		return triangle
	
	def setBone(self, idx:int, weight:float = 1.0):
		self.__boneIdx = idx
		self.__weight = weight
	
	def getString(self):
		return "\n".join((
			"triangles",
			"\n".join(t.getString(self.__boneIdx, self.__weight) for t in SafeIter(self, self.__triangles))
		))

class SourceModel(Terminable):
	def __init__(self, name:str, model, collision = None, allLODs:tuple = None):
		self.__name = name
		self.__model:Model = model
		self.__allLODs:tuple[Model] = allLODs
		self.__collision:CollisionGeom = collision
	
	def getQC(self, customPath:str, smdNames:dict[str, list[str]], lodSmdNames:list[tuple[float, dict[str, list[str]]]] = []):
		lines = [
			"$upaxis Y",
			"$origin 0 0 0 -90",
			# "$scale -1",
			"$opaque",
			f'$modelname "{customPath}/{self.name}.mdl"'
		]
		
		lines.append(f'$cdmaterials "{self.getCdMaterials(customPath)}"')

		lodLines:list[str] = [[f'$lod "{l[0] / SOURCE_UNITS_METERS}"', "{"] for l in SafeIter(self, lodSmdNames)]

		for smdBaseName in SafeIter(self, smdNames):
			for k, smdName in SafeEnumerate(self, smdNames[smdBaseName]):
				lines.append(f'$body studio "{smdName}.smd"')

				for lodIdx, lodData in SafeEnumerate(self, lodSmdNames):
					lodIdx:int
					lodDict:dict[str, list[str]] = lodData[1]
					curLodLines = lodLines[lodIdx]

					if not smdBaseName in lodDict or k >= len(lodDict[smdBaseName]):
						curLodLines.append(f'\treplacemodel "{smdName}.smd" "tinytriangle.smd"')
					else:
						curLodLines.append(f'\treplacemodel "{smdName}.smd" "{lodDict[smdBaseName][k]}.smd"')

		for k, v in SafeEnumerate(self, lodLines):
			v.append("}")

			lodLines[k] = "\n".join(v)
		
		lodLines:list[str]


		lines.append('$sequence "idle" "anims/idle.smd" loop')


		if self.__collision is not None:
			lines.append(f'$collisionmodel "{self.__collision.name}.smd"')
			lines.append("{")
			lines.append("\t$automass")
			lines.append("\t$concave")
			lines.append("}")

			surfaceProp = self.__collision.getSurfaceProp()
		else:
			log.log("No collision model found", LOG_WARN)
			surfaceProp = "default"
		
		lines.append(f'$surfaceprop "{surfaceProp}"')
		lines.append("\n".join(lodLines))

		return "\n".join(lines)

	def writeQC(self, outpath:str, customPath:str, smdNames:dict[str, list[str]], lodSmdNames:list[tuple[float, dict[str, list[str]]]] = []):
		filename = self.name + ".qc"
		filepath = path.join(outpath, filename)

		log.log(f"Generating {filename}")
		log.addLevel()

		qc = self.getQC(customPath, smdNames, lodSmdNames)

		self.__writeFile__(filepath, qc)

		log.subLevel()

		return filepath
	
	def __writeFile__(self, filepath:str, data:str):
		file = open(filepath, "w")
		file.write(data)
		file.close()

		log.log(f"Wrote {len(data)} bytes to {filepath}")

	def writeSMDs(self, outpath:str, model, skeSMD:str, lodIdx:int = 0):
		model:Model = model

		self.setSubTask(model)
		SMDs = model.getSMD(f"_lod{lodIdx}" if lodIdx > 0 else "")

		smdNames:dict[str, list[str]] = {}

		for smd in SafeIter(self, SMDs):
			if not smd.baseName in smdNames:
				smdNames[smd.baseName] = []
			
			smdNames[smd.baseName].append(smd.name)

		self.clearSubTask()

		fullSMD = "\n".join(("version 1", skeSMD))

		if self.__collision is not None and lodIdx == 0:
			self.setSubTask(self.__collision)
			colMdl = self.__collision.getModel(singleMesh = True)
			self.setSubTask(colMdl)
			SMDs.append(colMdl.getSMD(dontSplit = True)[0])

		for smd in SafeIter(self, SMDs):
			smd:SMD

			log.log(f"Generating {smd.name}.smd")
			log.addLevel()
			
			self.setSubTask(smd)

			# if skeleton is not None:
			# 	node = skeleton.getNodeByName(smd.name)

			# 	if node is not None:
			# 		log.log("Found parent bone")

					# smd.transform(node.wtm)
					# smd.setBone(node.idx)

			smdStr = "\n".join((fullSMD, smd.getString()))

			self.clearSubTask()
			
			self.__writeFile__(path.join(outpath, f"{smd.name}.smd"), smdStr)
			log.subLevel()

		return smdNames

	def getCdMaterials(self, customPath:str):
		return f"models/{customPath}/{self.name}"
	
	def getMaterialsDir(self, customPath:str):
		return f"materials/{self.getCdMaterials(customPath)}"

	def writeVMTs(self, outpath:str, customPath:str):
		if self.__model.materials is None:
			log.log("Model has no material, not generating VMTs")

			return

		outpath = path.join(outpath, "source_export")

		if not path.exists(outpath):
			mkdir(outpath)

		log.log("Generating VMTs")
		log.addLevel()

		materialsPath = path.join(outpath, self.getMaterialsDir(customPath))
		texturePaths = TexturePathDict(customPath)

		makedirs(materialsPath, exist_ok = True)


		for mat in SafeIter(self, self.__model.materials):
			mat:MaterialData

			filename = mat.getName() + ".vmt"

			log.log(f"Generating {filename}")
			log.addLevel()
			self.setSubTask(mat)
			fullVMT = mat.getVMT(texturePaths)
			
			self.clearSubTask()
			
			self.__writeFile__(path.join(materialsPath, filename), fullVMT)

			log.subLevel()

		log.subLevel()

		return texturePaths

	def getSkeletonSMD(self):
		skeleton = self.__model.skeleton

		if skeleton is None:
			skeSMD = "\n".join((
				"nodes",
				'0 "root" -1',
				"end",
				"skeleton", 
				"time 0", 
				"0 0 0 0 0 0 0",
				"end"
			))
		elif skeleton.nodeCount > MAX_SOURCE_BONES:
			log.log(f"SMD only supports {MAX_SOURCE_BONES} bones! (current: {skeleton.nodeCount})", LOG_ERROR)

			skeSMD = "\n".join((
				"nodes",
				'0 "modelHasTooManyBones" -1',
				"end",
				"skeleton", 
				"time 0", 
				"0 0 0 0 0 0 0",
				"end"
			))

			skeleton = None
		else:
			log.log("Generating skeleton SMD")
			log.addLevel()
			self.setSubTask(skeleton)
			skeSMD = skeleton.getSMD()
			self.clearSubTask()
			log.subLevel()
		
		return skeSMD
	
	def writeIdle(self, outpath:str, skeSMD:str):
		outdir = path.join(outpath, "anims")
		
		if not path.exists(outdir):
			mkdir(outdir)

		log.log("Writing idle anim")

		file = open(path.join(outdir, "idle.smd"), "w")
		file.write("version 1\n")
		file.write(skeSMD)
		file.close()
	
	def writeTinyTriangle(self, outpath:str):
		log.log("Writing tiny triangle")

		file = open(path.join(outpath, "tinytriangle.smd"), "w")
		file.write(TINY_TRIANGLE_SMD)
		file.close()


	def export(self, 
			outpath:str = getcwd(),
			customPath:str = "dae_out",
			exportCollisionModel:bool = False,
			exportSMD:bool = True) -> tuple[str, TexturePathDict]:
		outpath = path.join(outpath, self.name)

		if not path.exists(outpath):
			mkdir(outpath)

		if not exportCollisionModel:
			self.__collision = None
		
		log.log(f"Exporting {self.name} to Source")
		log.addLevel()
		
		texturePaths = self.writeVMTs(outpath, customPath)

		if exportSMD:
			skeSMD = self.getSkeletonSMD()
			self.writeIdle(outpath, skeSMD)

			if self.__allLODs is None:
				allLods = (self.model, )
			else:
				allLods = (self.model, *self.__allLODs)

				self.writeTinyTriangle(outpath)
			
			lodSmdNames:list[tuple[float, dict[str, list[str]]]] = []

			for lodId, model in SafeEnumerate(self, allLods):
				log.log(f"Exporting LOD {lodId} SMDs")
				log.addLevel()

				tempSmdNames = self.writeSMDs(outpath, model, skeSMD, lodId)

				if lodId == 0:
					smdNames = tempSmdNames
				else:
					lodSmdNames.append((model.distance, tempSmdNames))

				
				log.subLevel()
			
			qc = self.writeQC(outpath, customPath, smdNames, lodSmdNames)
		else:
			qc = None

		log.subLevel()

		return qc, texturePaths
		
	@property
	def model(self):
		return self.__model
	
	@property
	def name(self):
		return self.__name

class Model(Terminable):
	class Object:
		def __init__(self, name:str, skinned:bool):
			self.__name = name
			self.__skinned = skinned

			self.faces:list[tuple[int, int, int]] = []
			self.materials:dict[int, str] = {0:f"none_{name}"}
		
		@property
		def name(self):
			return self.__name
		
		@property
		def skinned(self):
			return self.__skinned
		
		@property
		def faceCount(self):
			return len(self.faces)
		
		def appendFace(self, face:tuple[int, int, int]):
			self.faces.append(face)
		
		def setFaces(self, faces:tuple[tuple[int, int, int]]):
			self.faces = faces
		
		def appendMaterial(self, materialName:str, startFaceIdx:int = None):
			if startFaceIdx == None:
				startFaceIdx = self.faceCount
			
			self.materials[startFaceIdx] = materialName
	
	DEFAULT_SCALE = (1, 1, 1)
	DEFAULT_OFFSET = (0, 0, 0, 0)

	def __init__(self,
		  	name:str,
		  	vertScale:tuple[float, float, float] = DEFAULT_SCALE, 
		  	skeleton = None,
			materials:tuple[MaterialData] = None,
			exportName:str = None,
			vertOffet:tuple[float, float, float, float] = DEFAULT_OFFSET,
			distance:float = 0.0):
		self.__vertScale = vertScale # tuple(v * VERTEX_SCALE for v in vertScale)
		self.__name = name
		self.__skeleton:GeomNodeTree = skeleton
		self.__materials = materials
		self.__vertOffet = vertOffet
		self.__distance = distance

		if exportName is None:
			exportName = name
		
		self.__exportName = exportName

		self.__objects:list[Model.Object] = []
		self.__vertices:list[tuple[float, float, float]] = []
		self.__uvs:list[tuple[float, float]] = []

	# 

	def __scaleVertex(self, vertex:tuple[float, float, float]):
		return (
				(vertex[0] * self.__vertScale[0]) + self.__vertOffet[0],
				(vertex[1] * self.__vertScale[1]) + self.__vertOffet[1],
				(vertex[2] * self.__vertScale[2]) + self.__vertOffet[2],
		 	  )

	def appendVerts(self, verts:Iterable[tuple[float, float, float]], UVs:Iterable[tuple[float, float]], scale:bool = True):
		if scale:
			verts = tuple(self.__scaleVertex(v) for v in verts)
		
		self.__vertices.extend(verts)
		self.__uvs.extend(UVs)
	
	def __getitem__(self, key:Union[int, str]):
		if isinstance(key, str):
			for obj in SafeIter(self, self.__objects):
				obj:Model.Object

				if obj.name == key:
					return obj
			
			return None
		else:
			return self.__objects[key]
	
	def newObject(self, name:str, skinned:bool = False):
		obj = self.Object(name, skinned)

		self.__objects.append(obj)

		return obj
	
	def mergeModel(self, mdl):
		mdl:Model = mdl
		
		vOfs = self.vertCnt

		log.log(f"Merging {mdl.name} with {self.name}")
		log.addLevel()

		self.appendVerts(mdl.__vertices, mdl.__uvs, scale = False)
		
		log.log(f"Appended {len(mdl.__vertices)} vertices and UVs (offset = {vOfs})")

		if mdl.materials is not None:
			if self.materials is None:
				self.__materials = mdl.materials
			else:
				self.__materials = (*self.materials, *mdl.materials)


		for obj in SafeIter(self, mdl):
			obj:Model.Object

			newObj = self.newObject(obj.name, obj.skinned)
			
			log.log(f"Merging {obj.name}'s {len(obj.faces)} faces")

			for startIdx in obj.materials:
				newObj.appendMaterial(obj.materials[startIdx], startIdx)
			
			for face in SafeIter(self, obj.faces):
				newObj.appendFace(tuple(idx + vOfs for idx in face))
		
		log.subLevel()

	def __getTuple__(self, listOfLists:list[list[tuple]], id:int):
		ofs = 0

		for list in SafeIter(self, listOfLists):
			cnt = len(list)

			if ofs + cnt > id:
				# print(ofs,cnt, id)
				return list[id - ofs]
			else:
				ofs += cnt

		raise IndexError(f"Can't find {id} ({ofs=})")
	
	def getUV(self, id:int) -> tuple[float, float, float]:
		return self.__uvs[id]
	
	def getVertex(self, id:int, scale:bool = False, parentBone = None) -> tuple[float, float, float]:
		v = self.__vertices[id]

		if parentBone is not None:
			v = vectorTransform(parentBone.wtm, v)

		if not scale:
			return v
		else:
			return (v[0] * VERTEX_SCALE, v[1] * VERTEX_SCALE, v[2] * VERTEX_SCALE)
	
	def getFaceCount(self):
		cnt = 0

		for obj in SafeIter(self, self.__objects):
			cnt += len(obj.faces)
		
		return cnt

	def __iter__(self):
		return self.__objects.__iter__()
	
	
	def getMaterialName(self, mat:int):
		if self.__materials is None:
			return f"unknown_{mat}"
		elif mat >= 0 and mat < len(self.__materials):
			return self.__materials[mat].getName()
		else:
			return f"error_{mat}"

	@classmethod
	def __join(cls, strList:Iterable[str]):
		return "\n".join(strList)
	
	# Model export

	def getOBJ(self):
		objVerts = [""] * self.vertCnt
		objUVs = [""] * self.vertCnt
		objNormals = ["vn 0.0 0.0 0.0"] * self.vertCnt
		objFaces = []
		
		normals = [None] * self.vertCnt
		normal = [None] * 3

		fV = lambda v: f"{v:.4f}"
		ff = lambda x: f"{x + 1}/{x + 1}/{x + 1}"

		skeleton = self.__skeleton

		faceOfs = 0

		for obj in SafeIter(self, self.__objects):
			obj:Model.Object

			objFaces.append(f"g {obj.name}")

			if skeleton is not None and not obj.skinned:
				parentNode = skeleton.getNodeByName(obj.name)
			else:
				parentNode = None

			for i in SafeRange(self, obj.faceCount):
				if i in obj.materials:
					objFaces.append(f"usemtl {obj.materials[i]}")
				
				f = obj.faces[i]

				for k, vId in SafeEnumerate(self, f):
					v = self.getVertex(vId, parentBone = parentNode)
					normal[k] = (v[0], v[1], -v[2])
					
					if objVerts[vId] == "":
						objVerts[vId] = f"v {fV(v[0])} {fV(v[1])} {fV(-v[2])}"

						uv = self.getUV(vId)

						objUVs[vId] = f"vt {fV(uv[0])} {fV(uv[1])}"
				
				self.generateNormal(normal, normals, f, populate = False)
				
				objFaces.append(f"f {ff(f[0])} {ff(f[1])} {ff(f[2])}")

		log.log("Normalizing normals")

		for k, v in SafeEnumerate(self, normals):
			if v is not None:
				normalize(v)
				
				objNormals[k] = f"vn {fV(v[0])} {fV(v[1])} {fV(v[2])}"


		return Model.__join((
			Model.__join(objVerts),
			Model.__join(objUVs),
			Model.__join(objNormals),
			Model.__join(objFaces),
		))
	
	def generateNormal(self, 
					normal:list[float, float, float], 
					normals:list[list[float, float, float]], 
					face:tuple[int, int, int],
					populate:bool = True):
		if populate:
			normal[0] = self.__vertices[face[0]]
			normal[1] = self.__vertices[face[1]]
			normal[2] = self.__vertices[face[2]]

		normal = getNormal(*normal)

		for idx in SafeIter(self, face):
			if normals[idx] is None:
				normals[idx] = [0, 0, 0]
			
			normals[idx][0] += normal[0]
			normals[idx][1] += normal[1]
			normals[idx][2] += normal[2]

	def getDMF(self):
		log.log("Writing model DMF")
		log.addLevel()
		

		normals = [None] * self.vertCnt
		normal = [None] * 3
		
		objBuffer = BBytesIO()

		for obj in SafeIter(self, self.__objects):
			obj:Model.Object

			log.log(f"Writing {obj.name} @ {objBuffer.tell()}")

			objBuffer.writeString(obj.name)
			objBuffer.writeInt(1 if obj.skinned else 0)
			objBuffer.writeInt(obj.faceCount)

			for f in SafeIter(self, obj.faces):
				objBuffer.write(pack("III", *f))

				self.generateNormal(normal, normals, f)

			objBuffer.writeInt(len(obj.materials))

			for faceIdx in obj.materials:
				matName = obj.materials[faceIdx]

				objBuffer.writeInt(faceIdx)
				objBuffer.writeString(str(matName))



		vertBuffer = BytesIO()
		normalBuffer = BytesIO()
		uvBuffer = BytesIO()
		
		for i in SafeRange(self, self.vertCnt):
			normal = normals[i]
			normalize(normal)

			vertBuffer.write(pack("fff", *self.__vertices[i]))
			normalBuffer.write(pack("fff", *normal))
			uvBuffer.write(pack("ff", *self.__uvs[i]))

		buffer = BBytesIO()
		buffer.write(DMF_MAGIC)

		buffer.write(pack("III", DMF_VDATA_OFS, 0x0, 0x0))
		buffer.write(pack("fff", *self.__vertScale[:3]))
		buffer.writeInt(self.vertCnt)
		
		buffer.write(vertBuffer.getvalue())
		buffer.write(normalBuffer.getvalue())
		buffer.write(uvBuffer.getvalue())

		buffer.writeInt(len(self.__objects))
		buffer.write(objBuffer.getvalue())

		value = buffer.getvalue()

		buffer.close()

		log.subLevel()

		return value

	def getMaterialDMF(self):
		if self.__materials is None:
			return None

		log.log("Generating material DMF")

		buffer = BBytesIO()

		buffer.writeInt(len(self.__materials))

		for mat in SafeIter(self, self.__materials):
			mat:MaterialData

			buffer.write(mat.getDMF())

		value = buffer.getvalue()

		buffer.close()

		return value
	
	def getSkeletonDMF(self):
		if self.__skeleton is None:
			return b""
		else:
			return self.__skeleton.getDMF()

	def getSMD(self, suffix:str = "", dontSplit:bool = False):
		log.log("Writing model SMD")
		log.addLevel()

		smdFiles:list[SMD] = []
		
		
		invY = lambda v: (v[0], v[1], -v[2])

		# normal = [None] * 3
		normals = [None] * self.vertCnt
		skeleton = self.__skeleton

		for obj in SafeIter(self, self.__objects):
			obj:Model.Object
			curMat = ""

			log.log(f"Building {obj.name} ({obj.faceCount} faces)")
			log.addLevel()
			
			subObjCnt = ((obj.faceCount * 3) // MAX_SOURCE_VERTS) + 1

			if subObjCnt > 1:
				if dontSplit:
					log.log(f"Model has too many verts, but we ignored cutting", LOG_WARN)

					subObjCnt = 1
				else:
					log.log(f"Model has too many verts, cutting into {subObjCnt} models")
			
			getFcnt = lambda id: (id * MAX_SOURCE_VERTS) // 3

			if skeleton is not None:
				parentBone = skeleton.getNodeByName(obj.name)
			else:
				parentBone = None

			for objId in SafeRange(self, subObjCnt):
				if subObjCnt > 1:
					smd = SMD(f"{obj.name}_{objId}{suffix}", obj.name)
					log.log(f"Building {objId + 1}/{subObjCnt}")
				else:
					smd = SMD(f"{obj.name}{suffix}", obj.name)
				
				for i in SafeRange(self, getFcnt(objId), min(getFcnt(objId + 1), obj.faceCount)):
					if i in obj.materials:
						curMat = obj.materials[i]
					
					f = obj.faces[i]
					
					verts = (
						invY(self.getVertex(f[0], True, parentBone if not obj.skinned else None)),
						invY(self.getVertex(f[1], True, parentBone if not obj.skinned else None)),
						invY(self.getVertex(f[2], True, parentBone if not obj.skinned else None))
					)
					
					self.generateNormal(verts, normals, f, populate = False)

					UVs = (
						self.getUV(f[0]),
						self.getUV(f[1]),
						self.getUV(f[2])
					)

					tri = smd.newTriangle(curMat, verts, UVs)

					tri.normals = (
						normals[f[0]],
						normals[f[1]],
						normals[f[2]]
					)

				if parentBone is not None and len(skeleton.getNodes()) <= MAX_SOURCE_BONES:
					smd.setBone(parentBone.idx)

				smdFiles.append(smd)
			
			log.subLevel()
		
		log.log("Normalizing normals")

		for normal in SafeIter(self, normals):
			if normal is not None:
				normalize(normal)


		log.subLevel()

		return smdFiles

	
	def exportObj(self, output:str = getcwd(), exportTexture:bool = True, forceConvert:bool = False):
		log.log(f"Exporting {self.exportName} as OBJ")
		log.addLevel()

		fileName = f"{self.exportName}.obj"

		obj = self.getOBJ()

		if self.materials is not None:
			log.log("Writing MTL")
			log.addLevel()

			mtl = MaterialTemplateLibrary(self.materials)
			
			file = open(output + "/" + self.exportName + ".mtl", "w")
			file.write(mtl.getMTL())
			file.close()

			if exportTexture:
				mtl.exportTextures(output, forceConvert)

			log.subLevel()

			obj = "mtllib " + self.exportName + ".mtl\n" + obj

		file = open(output + "/" + fileName, "w")
		file.write(obj)
		file.close()

		log.subLevel()

		log.log(f"Wrote {len(obj)} bytes to {fileName}")
	
	def exportDmf(self, output:str = getcwd(), exportTexture:bool = True, forceConvert:bool = False):
		log.log(f"Exporting {self.exportName} as DMF")
		log.addLevel()
		
		self.exportTextures(output, exportTexture, forceConvert = forceConvert)

		fileName = f"{self.exportName}.dmf"
		buffer = BBytesIO(self.getDMF())

		if self.materials is not None:
			buffer.seek(DMF_MAT_PTR, 0)
			matDmf = self.getMaterialDMF()
			buffer.writeInt(len(buffer.getvalue()))
			buffer.seek(0, 2)
			buffer.write(matDmf)
		
		if self.skeleton is not None:
			buffer.seek(DMF_SKE_PTR, 0)
			skeDmf = self.getSkeletonDMF()
			buffer.writeInt(len(buffer.getvalue()))
			buffer.seek(0, 2)
			buffer.write(skeDmf)

		dat = buffer.getvalue()
		buffer.close()

		file = open(output + "/" + fileName, "wb")
		file.write(dat)
		file.close()

		log.subLevel()

		log.log(f"Wrote {len(dat)} bytes to {fileName}")

	def exportTextures(self, 
		    output:str = getcwd(), 
		    exportTexture:bool = True,
		    texturePaths:TexturePathDict = None, 
			forceConvert:bool = False):
		if self.materials is not None and exportTexture:
			log.log("Exporting textures")
			log.addLevel()

			exported = set()

			for mat in SafeIter(self, self.materials):
				mat:MaterialData
				
				for tex in SafeIter(self, mat.textures):
					for i in SafeRange(self, 2): # im lazy
						if i == 0:
							texN = mat.checkModifiedName(MaterialData.getTexFileName(tex), texturePaths)

							if texN == tex:
								continue
						else:
							tex = tex

							texN = None
						
						if texN in exported:
							continue

						exported.add(texN)

						self.setSubTask(mat)
						mat.exportTexture(tex, output, forceConvert, texturePaths, texN)
						self.clearSubTask()

			log.subLevel()
	

	# properties

	@property
	def vertCnt(self):
		return len(self.__vertices)

	@property
	def skeleton(self):
		return self.__skeleton

	@property
	def distance(self):
		return self.__distance

	@property
	def materials(self):
		return self.__materials

	@property
	def exportName(self):
		return self.__exportName

	@property
	def name(self):
		return self.__name

class ModelContainer(ABC):
	@abstractmethod
	def getModel(self, lodId:int = 0) -> Model:
		...
	
	@property
	@abstractmethod
	def lodCount(self) -> int:
		...
	
	@abstractmethod
	def getExportName(self, lodId:int) -> str:
		return ...

	
class RealResData(Packed):
	@classmethod
	@property
	@abstractmethod
	def staticClassId(cls) -> int:
		...
	
	@property
	def classId(self) -> int:
		return self.staticClassId
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Real Resource Data"
	
	def __repr__(self):
		return f"<{self.name}\t{hex(self.classId)}\ts={self.size}\to={self.offset}>"

	def __init__(self, filePath:str, name:str = None, size:int = 0, offset:int = 0):
		super().__init__(filePath, name, size, offset)
		
		# self.setupClassName()

		self._setValid()


class UnknownResData(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x0
	
	@classmethod
	@property
	def classIconName(cls) -> str:
		return "unknown.bmp"

	@property
	def classId(self) -> int:
		return self.__classId
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "rrd"

	@classmethod
	@property
	def classNiceName(cls):
		return "Unknown RRD"
	
	@property
	def niceName(self):
		return f"{self.classNiceName} ({hex(self.__classId)})"
	
	@property
	def exportable(self):
		return False
	
	def __init__(self, filePath:str, name:str = None, size:int = 0, offset:int = 0, classId:int = 0xFFFFFFFF):
		super().__init__(filePath, name, size, offset)

		self.__classId = classId

class GeomNodeTree(RealResData, ModelContainer):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x56f81b6d
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "gnt"

	@classmethod
	@property
	def classNiceName(cls):
		return "Skeleton"

	@classmethod
	@property
	def classIconName(self):
		return "asset_skeleton.bmp"
	
	class Node: # not a terminable
		# def __iter__(self):
		# 	return self
		
		# def __next__(self):
		# 	raise StopIteration()
		
		def readMatrix4x4(self, file:BinBlock):
			flat = unpack("f" * 16, file.read(4 * 16))

			m = ( # transpose by hand, faster than a loop
				(flat[0], flat[4], flat[8], flat[12]),
				(flat[1], flat[5], flat[9], flat[13]),
				(flat[2], flat[6], flat[10], flat[14]),
				(flat[3], flat[7], flat[11], flat[15]),
			)

			return m
			# return tuple(zip(*([iter(flat)] * 4)))

			# return tuple(unpack("ffff", file.read(4 * 4)) for i in range(4))
		
		def readStringFuzzy(self, file:BinBlock):
			name = b""

			while file.tell() < file.getSize():
				b = file.read(1)
				
				if b == b"\x00":
					break
				
				name += b
			
			return name.decode("utf-8", errors='ignore') #Force the use of UTF-8 encoding

		def __init__(self, file:BinBlock, main:BinBlock, idx:int):
			self.tm = self.readMatrix4x4(file)
			self.wtm = self.readMatrix4x4(file)

			self.refOfs = readInt(file)
			self.refCnt = readInt(file)

			self.idx = idx

			file.seek(8, 1)

			self.pnt = readInt(file)

			file.seek(4, 1)

			nameOfs = readInt(file)

			ofs = main.tell()

			main.seek(nameOfs + 4, 0)

			self.name = self.readStringFuzzy(main)

			main.seek(ofs, 0)

			self.parent:GeomNodeTree.Node = None
			self.children = []
		
		def __repr__(self):
			return f"<{self.name}>"

		def __str__(self):
			result = self.name + ' { \n'

			result += '\ttm {\n'
			for column in self.tm:
				result += '\t\t' + ' '.join(map(str, column)) + '\n'
			result += '\t}\n'
			
			result += '\twtm {\n'
			for column in self.wtm:
				result += '\t\t' + ' '.join(map(str, column)) + '\n'
			result += '\t}\n'

			result += '}\n'

			return result

	def __init__(self, filePath:str, name:str = None, size:int = 0, offset:int = 0):
		super().__init__(filePath, name, size, offset)

		self.__nodes:list[GeomNodeTree.Node] = None
		self.__nodesDict:dict[str, GeomNodeTree.Node] = None
	
	def __retrieveData__(self):
		bfile = self.getBin()
		
		sz = readInt(bfile)
		flags = (sz & 0xFFFF_0000) >> 16
		sz &= 0x0000_FFFF

		file = bfile.readBlock()
		
		nodeCnt = readInt(file)

		# self.__nodes = dict(tuple(tuple((v.name, v) for v in (self.Node(file.readBlock(160), file), ))[0] for i in range(nodeCnt)), )
		nodes = tuple(self.Node(file.readBlock(160), file, i) for i in SafeRange(self, nodeCnt))
		
		
		self.__buildTree__(nodes)

		self.__nodes = nodes
		self.__nodesDict = {v.name:v for v in SafeIter(self, nodes)}
	
	def getNodeByName(self, name:str):
		if self.__nodes is None:
			self.__retrieveData__()
		
		if name in self.__nodesDict:
			return self.__nodesDict[name]
		
		return None
	
	def getNode(self, nodeId:int):
		return self.getNodes()[nodeId]

	def getNodes(self):
		if self.__nodes is None:
			self.__retrieveData__()
		
		return self.__nodes

	def __buildTree__(self, nodes:dict):
		for nodeName in SafeIter(self, nodes):
			# node:GeomNodeTree.Node = nodes[nodeName]
			node:GeomNodeTree.Node = nodeName

			childOfs = node.refOfs // 160
			childCnt = node.refCnt

			if childCnt != 0:
				for i in SafeRange(self, childCnt):
					try:
						child:GeomNodeTree.Node = nodes[childOfs + i]
					except:
						break

					child.parent = node
					node.children.append(child)

	def print_tree(self, root, indent = 0):
		t = "\t"

		print(f"{t * indent} {root.idx}:{root.name}")

		for child in root.children:
			self.print_tree(child, indent + 1)
	
	def getDMF(self):
		log.log("Generating skeleton DMF")

		buffer = BBytesIO()

		buffer.writeInt(len(self.getNodes()))

		for node in self.getNodes():
			parentIdx = (DMF_NO_PARENT if node.parent is None else node.parent.idx) + 1

			buffer.writeInt(parentIdx)
			buffer.writeString(node.name)

			for tm in node.wtm:
				buffer.write(pack("4f", *tm))

		value = buffer.getvalue()
		buffer.close()

		return value
	
	def getModel(self, lodId:int = 0):
		return Model(self.name, skeleton = self)

	def getExportName(self, lodId:int):
		return self.name

	def getSMD(self):
		skeSMD = ["nodes"]

		posStr = ["skeleton", "time 0"]

		f = lambda x: f"{x:.4f}"
		s = lambda x: f(x * VERTEX_SCALE)

		for node in SafeIter(self, self.getNodes()):
			node:GeomNodeTree.Node

			skeSMD.append(" ".join((
				str(node.idx),
				f'"{node.name if node.idx != 0 else "_ROOT"}"',
				str(node.parent.idx if node.parent is not None else -1)
			)))

			euler = matrixToEuler(node.wtm)
			
			posStr.append(" ".join((
				str(node.idx),
				s(node.tm[0][3]),
				s(node.tm[1][3]),
				s(node.tm[2][3]),
				f(euler[0]),
				f(euler[1]),
				f(euler[2]),
			)))
		skeSMD.append("end")
		posStr.append("end")

		return "\n".join(("\n".join(skeSMD), "\n".join(posStr)))

	@property
	def nodeCount(self):
		return len(self.getNodes())
	
	@property
	def lodCount(self):
		return 1

class RendInst(RealResData, ModelContainer):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x77f8232f
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "ri"

	@classmethod
	@property
	def classNiceName(cls):
		return "Renderable instance"

	@classmethod
	@property
	def classIconName(cls):
		return "asset_rendinst.bmp"
	
	def __init__(self, filePath:str, name:str = None, size:int = 0, offset:int = 0):
		super().__init__(filePath, name, size, offset)

		self.__dataComputed:bool = False
		self.__textures:list[str] = None
		self.__materials:list[MaterialData] = None
		self.__collisionGeom:GeomNodeTree = None

		self.__texCnt = 0
		self.__matCnt = 0

		self._setValid()

	@property
	def dataComputed(self):
		return self.__dataComputed

	def _setDataComputed(self):
		self.__dataComputed = True

	def computeData(self):
		if self.dataComputed:
			return

		log.log(f"Loading {self.name}")
		log.addLevel()

		file = self.getBin()

		self._findCollisionGeom()

		self._readHeader(file)
		self._readMatVData(file)
		self._readModelData(file)
		self._readShaderMesh(file)

		self._setDataComputed()

		log.subLevel()

	def _readHeader(self, file:BinBlock):
		log.log("Loading header")
		log.addLevel()

		lodHdrInfo = readInt(file)
		lodHdrSz = lodHdrInfo & 0xFFFFFF

		riStructSzAdd = 48

		if (lodHdrInfo & 0x1000000) != 0:
			riStructSzAdd = 184
		
		altStructSz = lodHdrSz + riStructSzAdd
		riStructSz = 160 #RenderableInstanceLodsResource default size. if the hdr info has something in it, then increase the struct sz 

		if altStructSz > 0xA0:
			riStructSz = altStructSz
		
		lodHdrSz = lodHdrInfo

		self.__riStructSz = riStructSz
		self.__lodHdrSz = lodHdrSz

		texCnt = readInt(file)
		matCnt = readInt(file)
		
		self.vdataNum = readInt(file)
		
		num = readInt(file)
		self.mvdHdrSz = num & 0x3FFFFFFF
		self.mvdHdrFlag = num != self.mvdHdrSz
		# self.mvdHdrNum = num

		if texCnt == matCnt == 0xFFFFFFFF:
			log.log("Pulling material and texture count from GameResDesc") # TODO

			mats = AssetCacher.getModelMaterials(self.name)

			if mats is None or len(mats) == 0:
				self._setTextureCount(0)
				self._setMaterialCount(0)

				self._setMaterials(None)
			else:
				self._setTextures(AssetCacher.getModelTextures(self.name))
				self._setMaterials(mats)

		else:
			self._setTextureCount(texCnt)
			self._setMaterialCount(matCnt)

			self._readTextures(file)

		log.subLevel()
	
	def _readTextures(self, file:BinBlock):
		texMapSz = readInt(file)
		texIndicesOfs = readInt(file)
		texCnt = readInt(file)
		
		file.seek(8, 1)
		
		nameMap = readNameMap(file, texCnt, texIndicesOfs + 0x18, 0x10, self) # TODO: use readNameMap

		self._setTextures(nameMap)

	def _setTextures(self, textures:list[str]):
		self.__textures = textures

		self._setMaterialCount(len(textures) if textures is not None else 0)
	
	@property
	def textures(self):
		return self.__textures


	def _readMatVData(self, file:BinBlock):
		self.mvdOfs = file.tell()

		if self.vdataNum == 0:
			log.log("vDataNum == 0, skipping MVD")

			self._setMatVData(None)

			file.seek(0x20, 1)

			return

		log.log("Loading MatVData")
		log.addLevel()

	
		if not self.mvdHdrFlag:
			log.log("MVD flag not raised", LOG_WARN)

			file.seek(0x10, 1)
		else:
			bin = CompressedData(file).decompressToBin()

			if bin is not None:
				mvd = MatVData(bin, self.name, self.textureCount, self.materialCount, textures = self.textures)
				
				self._setMatVData(mvd)
			else:
				log.log("MVD Bin is None", LOG_ERROR)
		
		log.subLevel()

	def _readModelData(self, file:BinBlock):
		ofs = file.tell()

		data = file.readBlock(readInt(file) - 4)

		self._setLodCount(readInt(data))

		data.seek(8, 1) # ptr

		bbox = (unpack("fff", data.read(0xC)), unpack("fff", data.read(0xC)))
		bsphCenter = unpack("fff", data.read(0xC))
		bsphRad = unpack("f", data.read(4))
		bound0rad = unpack("f", data.read(4))

		impostorDataOfs = readInt(data)
		
		self.__occ = tuple(self._readOcc(file) for i in SafeRange(self, self.lodCount))
		
		if impostorDataOfs > 0:
			file.seek(ofs + impostorDataOfs, 0)

			impostorShaderMeshOfs = readLong(file)
			impostorSz = readLong(file)

			self._readImposorData(file.readBlock(impostorSz - (file.tell() - ofs)))
	
	def _readOcc(self, file:BinBlock):
		occ = unpack("Qf", file.read(0xC))

		file.seek(0x4, 1)

		return occ
	
	def _readImposorData(self, file:BinBlock):
		# cMethod = readLong(file)

		# impostor = CompressedData(file.readRest(), 0x60).decompressToBin()
		# impostor.quickSave("impostor.imp")

		...

	def _readShaderMesh(self, file:BinBlock):
		log.log(f"Processing {self.lodCount} shadermesh resources")
		log.addLevel()

		self.__shaderMesh = tuple(InstShaderMeshResource(file) for i in SafeRange(self, self.lodCount))

		log.subLevel()

	def _generateMVDmaterials(self, mvd:MatVData):
		mvd.computeData()

		materials = mvd.getMaterials()
		computeMaterialNames(materials, self)

		return materials

	def generateMaterials(self):
		if self.materials is not None or self.textures is None:
			return False

		materials = None

		if self.mvd is not None:
			materials = self._generateMVDmaterials(self.mvd)
		
		if materials is not None and materials == tuple():
			materials = None

		self._setMaterials(materials)

		return True

	def _computeMvdShaderMesh(self, 
							mvd:MatVData, 
							shaderMeshElems:tuple[ShaderMesh.Elem],
							mdl:Model,
							obj:Model.Object,
							vertexDatas:list[list[MatVData.VertexData, int]],
							vertexOrder:dict[int, list[ShaderMesh.Elem]],
							vOfs:int = 0):
		
		for k, elem in SafeEnumerate(self, shaderMeshElems):
			elem:ShaderMesh.Elem
			
			if not elem.vdOrderIndex in vertexOrder:
				vertexOrder[elem.vdOrderIndex] = []

			if vertexDatas[elem.vData] == None:
				vertexData = mvd.getVertexData(elem.vData)

				verts, UVs = vertexData.getVertices(), vertexData.getUVs()

				mdl.appendVerts(verts, UVs)

				vertexDatas[elem.vData] = (vertexData, vOfs)

				vOfs += len(verts)
			
			vertexOrder[elem.vdOrderIndex].append(elem)
			
		for orderElems in SafeIter(self, vertexOrder.values()):
			orderElems:list[ShaderMesh.Elem]
			
			for elem in SafeIter(self, orderElems):
				vertexData = vertexDatas[elem.vData][0]
				indiceOffset = vertexDatas[elem.vData][1] + elem.baseVertex

				faces = vertexData.getFaces()

				curFace = elem.startI // 3

				obj.appendMaterial(mdl.getMaterialName(elem.mat))
				
				for i in SafeRange(self, curFace, curFace + elem.numFace):
					obj.appendFace(tuple(f + indiceOffset for f in faces[i]))
		
		return vOfs
	
	def getModel(self, lodId:int):
		self.computeData()
		self.generateMaterials()

		if self.materials is None and self.textures is None:
			log.log("No materials were loaded: material groups will be unnamed", LOG_WARN)

		mdl = Model(self.name, 
		  materials = self.materials,
		  exportName = self.getExportName(lodId),
		  distance = self.__occ[lodId][1])
		obj = mdl.newObject(self.name)

		mvd = self.mvd
		mvd.computeData()
		shaderMeshElems = self.__shaderMesh[lodId].shaderMesh.elems

		vertexDatas:list[list[MatVData.VertexData, int]] = [None for i in SafeRange(self, mvd.getVDCount())]
		vertexOrder:dict[int, list[ShaderMesh.Elem]] = {}

		self._computeMvdShaderMesh(mvd, shaderMeshElems, mdl, obj, vertexDatas, vertexOrder)

		return mdl
	
	def getExportName(self, lodId:int):
		return f"{self.name}_{lodId}"

	def getSourceModel(self, lodId:int = 0, exportLODs:bool = False):
		if exportLODs and self.lodCount > 1:
			allLODs = tuple(self.getModel(i) for i in SafeRange(self, 1, self.lodCount))
		else:
			allLODs = None
		
		return SourceModel(self.name, self.getModel(lodId), self.collision, allLODs)
	

		
	def _setLodCount(self, cnt:int):
		self.__lodCnt = cnt
	
	@property
	def lodCount(self):
		return self.__lodCnt
	
	def _setTextureCount(self, cnt:int):
		self.__texCnt = cnt
	
	def _setMaterialCount(self, cnt:int):
		self.__matCnt = cnt
	
	@property
	def materialCount(self):
		return self.__matCnt
	
	@property
	def textureCount(self):
		return self.__texCnt
	
	
	def _setMatVData(self, mvd:MatVData):
		self.__mvd = mvd
	
	@property
	def mvd(self):
		return self.__mvd


	def _setMaterials(self, materials:list[MaterialData]):
		self.__materials = materials

		self._setMaterialCount(len(materials) if materials is not None else 0)
	
	
	@property
	def materials(self):
		return self.__materials

	def _getCachedAsset(self, objType:type, suffix:str):
		assets = AssetCacher.getCachedAsset(objType, f"{self.name}{suffix}")
		
		if not assets or assets == []:
			name = self.name.split("_")

			if name[-1] == "dynmodel":
				assets = AssetCacher.getCachedAsset(objType, f"{'_'.join(name[:-1])}{suffix}")
		
		if not assets or assets == []:
			log.log(f"Could not find '{suffix}' asset", LOG_WARN)

			return None
		else:
			return assets[0]
	
	@property
	def collision(self):
		return self.__collisionGeom

	def setCollisionGeom(self, col):
		self.__collisionGeom = col
	
	def _findCollisionGeom(self):
		self.setCollisionGeom(self._getCachedAsset(CollisionGeom, "_collision"))

class DynModel(RendInst):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0xb4b7d9c4
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "dyn"

	@classmethod
	@property
	def classNiceName(cls):
		return "Dynamic model" # Dynamic renderable scene

	@classmethod
	@property
	def classIconName(self):
		return "asset_dynmodel.bmp"
	
	class Lod:
		class RigidObject:
			def __init__(self, file:BinBlock):
				self.shaderMeshPtr = readLong(file)
				self.sph_c = unpack("fff", file.read(4 * 3))
				self.sph_r = unpack("f", file.read(4))[0]
				self.nodeId = readInt(file)
				self._resv = readInt(file)

		def __init__(self, file:BinBlock, lodIdx:int, occData:tuple[int, float]):
			log.log(f"Processing LOD {lodIdx}")
			log.addLevel()

			ofs = file.tell()

			hdrSz = readInt(file)
			rigidCnt = readInt(file)

			file.seek(8, 1)

			self.skinOfs = readInt(file)
			self.skinCnt = readInt(file)

			file.seek(8, 1)

			log.log(f"Loading {rigidCnt} rigid objects {self.skinCnt} skins @ {self.skinOfs}")
			log.addLevel()

			self.rigids = tuple(self.RigidObject(file.readBlock(0x20)) for i in range(rigidCnt))

			file.seek(occData[0] - (file.tell() - ofs), 1)
			
			log.subLevel()
			
			log.log(f"Loading {rigidCnt} shader mesh resources")
			log.addLevel()

			self.shaderMesh = tuple(ShaderMesh(file) for i in range(rigidCnt))

			log.subLevel()

			log.subLevel()
	

	def generateMaterials(self):
		if (not super().generateMaterials() 
			or self.materials is not None
			or self.skinnedMeshRes is None):
			return

		self._setMaterials(self._generateMVDmaterials(self.skinnedMeshRes.mvd))
	
	def computeData(self):
		if self.dataComputed:
			return
		
		file = self.getBin()

		self._findGeomNodeTree()
		self._findCollisionGeom()

		self._readHeader(file)
		self._readMatVData(file)
		self._readModelData(file)
		self._readSceneNodes(file.readBlock(readInt(file)))
		self._readLods(file)
		self._readSkinnedMesh(file)

		self._setDataComputed()
	
	def _findGeomNodeTree(self):
		self.setGeomNodeTree(self._getCachedAsset(GeomNodeTree, "_skeleton"))

	def _readModelData(self, file:BinBlock):
		blockSz = readInt(file)

		data = file.readBlock(blockSz - 4)

		self._setLodCount(readInt(data))

		data.seek(8, 1) # ptr

		bbox = (unpack("fff", data.read(0xC)), unpack("fff", data.read(0xC)))

		if blockSz > 0x28:
			self.__bpC254 = unpack("ffff", data.read(0x10))
			self.__bpC255 = unpack("ffff", data.read(0x10))
		else:
			self.__bpC254 = (0, 0, 0, 0)
			self.__bpC255 = (1.0, 1.0, 1.0, 1.0)
		
		log.log(f"bpC254: {self.__bpC254}")
		log.log(f"bpC255: {self.__bpC255}")
		
		self.__occ = tuple(self._readOcc(file) for i in SafeRange(self, self.lodCount))
	

	def _readSceneNodes(self, file:BinBlock):
		log.log("Processing scene nodes")
		log.addLevel()

		indicesOfs = readInt(file)
		nameCnt = readInt(file)

		file.seek(8, 1)

		skinNodesOfs = readInt(file)
		skinNodeCnt = readInt(file)

		file.seek(8, 1)

		nameMapData = file.read(indicesOfs - 0x20)

		nameMap = []

		prev = readLong(file) - 0x20
		# TODO: read name map
		for i in SafeRange(self, nameCnt):
			next = nameCnt == i + 1 and -1 or readLong(file) - 0x20
			
			nameMap.append(nameMapData[prev:next].decode("utf-8").rstrip("\x00"),)
			
			prev = next
		
		self.__nodeNames = nameMap
		
		log.log(f"Processed {nameCnt} nodes")
		
		self.__skinNodes = {readShort(file):i for i in SafeRange(self, skinNodeCnt)}

		log.log(f"Processed {skinNodeCnt} skin nodes")

		file.seek(((4 - (skinNodeCnt % 4)) * 2) % 8, 1)

		log.subLevel()

	def _readLods(self, file:BinBlock):
		lodCnt = self.lodCount

		log.log(f"Processing {lodCnt} LODs")
		log.addLevel()
		
		self.__lods = tuple(self.Lod(file, i, self.__occ[i]) for i in SafeRange(self, lodCnt))

		log.subLevel()

	def _readSkinnedMesh(self, mfile:BinBlock):
		self.__skinnedMeshRes:SkinnedMeshResource = None

		if mfile.tell() + 4 >= mfile.getSize():
			return
		
		cnt = readInt(mfile)

		if cnt == 0:
			return
		elif cnt > 1:
			raise Exception("Multiple skinned mesh!")

		log.log(f"Loading skinned mesh resource @ {mfile.tell()}")
		log.addLevel()


		self.skinnedMeshOfs = mfile.tell()

		file = mfile.readBlock(readInt(mfile))
		skinnedMeshRes = SkinnedMeshResource(self.name)
		self.setSubTask(skinnedMeshRes)
		skinnedMeshRes.computeData(file, self.mvdHdrFlag, self.textures, self.__lods)
		self.clearSubTask()

		self.__skinnedMeshRes = skinnedMeshRes
		

		log.subLevel()

	def setGeomNodeTree(self, skeleton:GeomNodeTree):
		self.__skeleton = skeleton

	@property
	def skinnedMeshRes(self):
		return self.__skinnedMeshRes

	@property
	def geomNodeTree(self):
		return self.__skeleton

	def getSkinnedMeshModel(self, lodId:int):
		smr = self.__skinnedMeshRes

		if smr is None or len(smr.skinnedMesh) <= lodId:
			log.log("No skinned mesh found")

			return None
		
		mvd = smr.mvd

		if mvd.hasMaterials:
			materials = mvd.getMaterials()
		else:
			materials = AssetCacher.getSkinnedMaterials(self.name)
		
		computeMaterialNames(materials, self)
		
		mdl = Model(f"{self.name}_skinnedmesh", 
		  			vertScale = self.__bpC255,
					vertOffet = self.__bpC254,
					materials = materials)

		log.log(f"Gathering vertex data for skinned MVD")
		log.addLevel()

		skinnedMesh = smr.skinnedMesh[lodId]

		vertexDatas:list[list[MatVData.VertexData, int]] = [None for i in SafeRange(self, mvd.getVDCount())]
		vOfs = 0
		
		for i, nodeId in SafeEnumerate(self, skinnedMesh.skinNodes):
			i:int
			nodeId:int

			name = self.__nodeNames[self.__skinNodes[nodeId]]

			obj = mdl.newObject(name, skinned = True)
			log.log(f"Exporting Skinnedmesh {name}")
			log.addLevel()
			shaderMeshElems:tuple[ShaderMesh.Elem] = skinnedMesh.mesh[i].elems
			
			
			vOfs = self._computeMvdShaderMesh(mvd, shaderMeshElems, mdl, obj, vertexDatas, {}, vOfs)

			log.subLevel()

		log.subLevel()

		return mdl

	def getModel(self, lodId:int):
		self.computeData()
		self.generateMaterials()

		mdl = Model(self.name,
		  			vertScale = self.__bpC255,
					skeleton = self.geomNodeTree,
					materials = self.materials,
					exportName = self.getExportName(lodId),
					vertOffet = self.__bpC254,
					distance = self.__occ[lodId][1])

		log.log(f"Generating LOD {lodId} OBJ for {self.name}")
		log.addLevel()
		
		if self.materials is None:
			log.log("No materials were loaded: material groups will be unnamed", LOG_WARN)
		
		vOfs = 0
		skinnedMdl = self.getSkinnedMeshModel(lodId)
		mvd = self.mvd

		if mvd is not None:
			mvd.computeData()

			lod = self.__lods[lodId]
			
			
			vertexDataCnt = mvd.getVDCount()
			lodShaderMesh = lod.shaderMesh
			vertexDatas:list[list[MatVData.VertexData, int]] = [None for i in SafeRange(self, vertexDataCnt)]


			log.log("Processing nodes")
			log.addLevel()

			lodVertexOrder:list[dict[int, list[ShaderMesh.Elem]]] = [{} for _ in lodShaderMesh]
			
			for shaderMeshId, shaderMesh in SafeEnumerate(self, lodShaderMesh):
				vertexOrder = lodVertexOrder[shaderMeshId]
				shaderMesh:ShaderMesh

				rigid = lod.rigids[shaderMeshId]
				nodeId = self.__skinNodes[rigid.nodeId]

				name = self.__nodeNames[nodeId]
				
				log.log(f"Processing rigid {name}")
				# log.log(f"Processing rigid {shaderMeshId=} {lod.rigids[shaderMeshId].nodeId=} {self.__skinNodes[lod.rigids[shaderMeshId].nodeId]=} {self.__skinNodes[shaderMeshId]=} {name}")
				log.addLevel()

				obj = mdl.newObject(name)
				
				# VirtualDynModelEntity::setup

				vOfs = self._computeMvdShaderMesh(mvd, shaderMesh.elems, mdl, obj, vertexDatas, vertexOrder, vOfs)

				log.subLevel()
			
			
			log.subLevel()
		
		log.subLevel()

		if skinnedMdl is not None:
			mdl.mergeModel(skinnedMdl)

		return mdl


class CollisionGeom(RealResData, ModelContainer):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0xace50000
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "col"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Collision geometry" # Dynamic renderable scene

	@classmethod
	@property
	def classIconName(self):
		return "asset_collision.bmp"
	

	def __init__(self, filePath:str, name:str = None, size:int = 0, offset:int = 0):
		super().__init__(filePath, name, size, offset)

		self.__nodes:tuple[CollisionGeom.CollNode] = None

		self._setValid()
	
	VERSIONS = {
		538968832,
		538445072,
		538312992,
		538247445,
		537397541
	}
		

	class CollNode(Terminable):
		def readString(self, file:BinFile):
			sz = readInt(file)
			text = file.read(sz).decode()

			if sz & 3:
				file.seek(4 - (sz & 3), 1)
			
			return text
		def readBlock(self, file:BinBlock, collisionFlags:int, version:int):
			self.__name = self.readString(file)
			
			if version >= 538247445:
				self.physMat = self.readString(file)
			else:
				self.physMat = None
			
			collisionType = readInt(file)

			if collisionFlags & 2 != 0:
				behaviorFlags = readByte(file)
			
			tm = (
				(*unpack("fff", file.read(0xC)), 0),
				(*unpack("fff", file.read(0xC)), 0),
				(*unpack("fff", file.read(0xC)), 0),
				(*unpack("fff", file.read(0xC)), 1)
			)

			# print("COLTRANSFORM", self.__name, tm)
			
			if collisionFlags & 4 != 0:
				file.seek(48, 1)

			bSphere = (unpack("fff", file.read(0xC)), *unpack("ff", file.read(0x8)))
			self.bbox = (unpack("fff", file.read(0xC)), unpack("fff", file.read(0xC)))
			
			scaleFix = (
				(1, 0, 0, 0),
				(0, 0, 1, 0),
				(0, 1, 0, 0),
				(0, 0, 0, 1),
			)

			tm = matrixMul(tm, scaleFix)
			pos = bSphere[0]
			# print("POSOFS", bSphere)

			vCnt = readInt(file)
			
			def getVert(v):
				v = vectorTransform(tm, v)
				v = (
					v[0],
	 				v[2],
					v[1])

				return v

			self.__verts:tuple[tuple[float, float, float]] = tuple(getVert(unpack("3f", file.read(0xC))) for _ in SafeRange(self, vCnt))
			idxCnt = readInt(file) // 3
			self.__faces:tuple[tuple[int, int, int]] = tuple(unpack("3I", file.read(0xC)) for _ in SafeRange(self, idxCnt))

		def getVerts(self):
			return self.__verts
	
		def getFaces(self):
			return self.__faces

		@property
		def name(self):
			return self.__name

	def __readFile__(self):
		if self.__nodes is not None:
			return
		
		file = self.getBin()

		magic = readInt(file)

		if magic != self.classId:
			raise Exception(f"Invalid magic: {magic}")
		
		version = readInt(file)

		if not version in CollisionGeom.VERSIONS:
			raise Exception(f"Invalid version: {version}")

		bSphereSz = readInt(file)

		bSphere = (unpack("fff", file.read(0xC)), *unpack("ff", file.read(0x8)))

		block = file.readBlock(readInt(file))

		if version >= 538445072:
			collisionFlags = readInt(block)
		else:
			collisionFlags = 0
		
		# self.__version = version
		# self.__collisionFlags = collisionFlags

		nodeCnt = readInt(block)

		self.__nodes = tuple(self.__createNode__(block, collisionFlags, version) for _ in SafeRange(self, nodeCnt))
	
	def __createNode__(self, block:BinBlock, collisionFlags:int, version:int):
		node = CollisionGeom.CollNode()
		self.setSubTask(node)
		node.readBlock(block, collisionFlags, version)
		self.clearSubTask()

		return node

	def getSurfaceProp(self):
		self.__readFile__()

		# for node in self.__nodes:
		# 	print(node.physMat)

		return "default"

	def getSkeleton(self):
		suffix = "skeleton"

		assets = AssetCacher.getCachedAsset(GeomNodeTree, f"{'_'.join(self.name[:-1])}{suffix}")
		
		if not assets or assets == []:
			log.log(f"Could not find '{suffix}' asset", LOG_WARN)

			return None
		else:
			return assets[0]
		
	
	def getModel(self, lodId:int = 0, singleMesh:bool = False):
		self.__readFile__()

		# print(self.getSurfaceProp())

		mdl = Model(self.name, skeleton = self.getSkeleton())
		
		vOfs = 0

		if singleMesh:
			ob = mdl.newObject(self.name)

		for node in SafeIter(self, self.__nodes):
			node:CollisionGeom.CollNode

			if not singleMesh:
				ob = mdl.newObject(node.name)
			
			verts = node.getVerts()

			mdl.appendVerts(verts, tuple((0, 0) for _ in SafeRange(self, len(verts))))

			ob.appendMaterial(f"{self.name}_{node.name}_{node.physMat if node.physMat is not None else 'unknown'}")

			for f in SafeIter(self, node.getFaces()):
				ob.appendFace((
								f[0] + vOfs,
		   						f[1] + vOfs,
		   						f[2] + vOfs))

			vOfs += len(verts)
		
		return mdl
	
	def getExportName(self, lodId:int):
		return self.name
	
	@property
	def lodCount(self):
		return 1
	
class FX(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x88b7a117
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "fx"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Particle effect"

	@classmethod
	@property
	def classIconName(cls):
		return "asset_fx.bmp"

class PhysObj(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0xd543e771
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "phy"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Physics object"

	@classmethod
	@property
	def classIconName(cls):
		return "asset_physobj.bmp"

class FastPhys(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x855a1be6
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "fphy"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Fast physics"

	@classmethod
	@property
	def classIconName(self):
		return "asset_fastphys.bmp"

class Anim2Data(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x40c586f9
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "a2d"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Anim2Data"

	@classmethod
	@property
	def classIconName(self):
		return "asset_a2d.bmp"

class AnimChar(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x8f2a701a
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "anm"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "AnimChar"

	@classmethod
	@property
	def classIconName(self):
		return "asset_animTree.bmp"

class Char(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0xa6f87a9b
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "char"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Character"

	@classmethod
	@property
	def classIconName(self):
		return "asset_character.bmp"

class LandClass(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x3fb59c4
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "lcls"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Land class"

	@classmethod
	@property
	def classIconName(self):
		return "asset_land.bmp"

class MaterialClass(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x39b5b09e
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "mtl"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Material"

	@classmethod
	@property
	def classIconName(self):
		return "asset_mat.bmp"

class ImpostorData(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0x2ad457f0
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "imp"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Impostor data"

	@classmethod
	@property
	def classIconName(self):
		return "asset_rndgrass.bmp"

class ShaderGraph(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0xcd5b9736
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "sdr"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Shader graph"

	@classmethod
	@property
	def classIconName(self):
		return "asset_shaderGraph.bmp"

class RndGrass(RealResData):
	@classmethod
	@property
	def staticClassId(cls) -> int:
		return 0xa7d3ed6a
	
	@classmethod
	@property
	def fileExtension(cls) -> str:
		return "rnd"
	
	@classmethod
	@property
	def classNiceName(cls):
		return "Random grass"

	@classmethod
	@property
	def classIconName(self):
		return "asset_rndgrass.bmp"

# unknown classes :
# 0x73a10e01
# 0x5dff175


REALRES_CLASSES_LIST:tuple[type[RealResData]] = (
	RendInst,
	DynModel,
	CollisionGeom,
	FX,
	GeomNodeTree,
	PhysObj,
	FastPhys,
	Anim2Data,
	AnimChar,
	LandClass,
	MaterialClass,
	ImpostorData,
	ShaderGraph,
	RndGrass, # look for getResClassId in IDA
	Char
)

REALRES_CLASSES_DICT:dict[int, type[RealResData]] = {v.staticClassId:v for v in REALRES_CLASSES_LIST}
