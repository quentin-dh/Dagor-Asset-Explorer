import sys
from os import path, getcwd

sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))

import util.log as log
from util.fileread import *
from util.enums import *
from parse.datablock import *
from parse.realres import RealResData, UnknownResData, REALRES_CLASSES_DICT
from util.terminable import SafeRange, Pack, Terminable, FilePathable, SafeEnumerate, SafeIter
from util.decompression import zstdDecompress
from parse.material import MaterialData, computeMaterialNames


class GameResDesc(FilePathable, Terminable):
	def __init__(self, filePath:str):
		super().__init__(filePath)

		self.__datablock = None
	
	def __getDecompressed__(self):
		file = open(self.filePath, "rb")


		if readByte(file) != 2:
			file.close()

			raise Exception("bin magic != 2")

		log.log("Decompressing...")
		
		size = readEx(3, file) # - 5
		decompressed = BinFile(zstdDecompress(file.read(size)))

		# if readByte(file) != 1:
		# 	file.close()
			
		# 	raise Exception("data magic != 1")

		file.close()

		return decompressed
	
	def loadDataBlock(self):
		log.log(f"Reading GameResDesc {self.name}")
		log.addLevel()

		self.__datablock = loadDataBlock(self.__getDecompressed__())

		log.subLevel()
	
	def getDataBlock(self):
		return self.__datablock

	def getModelTextures(self, model:str) -> list[str]:
		blk = None
		tex = None

		try:
			blk = self.__datablock.getByName(model)
			tex = blk.getByName("tex")

			if tex is None:
				raise Exception
		except Exception as er:
			return tuple()

		return tuple(tex.getParamById(i)[1] for i in SafeRange(self, tex.getParamsCount()))
	
	def hasName(self, model:str):
		try:
			if self.__datablock.getByName(model) != None:
				return True
		except:
			return False

	def __exploreMaterialBlock(self, blk:DataBlock, blockName:str, tex:list[str], mats:list[MaterialData]):
		matB = blk.getByName(blockName)

		if matB is None:
			log.log(f"No such block '{blockName}', skipping")

			return
		
		for matBlock in SafeIter(self, matB.getChildren()):
			matBlock:DataBlock
			mat = MaterialData()


			log.log(f"{matBlock}")
			log.addLevel()

			for i in SafeRange(self, matBlock.getParamsCount()):
				flags, params = matBlock.getParamById(i)
				blockName = matBlock.getParamNameByFlags(flags)
				blockType = flags & 0xF000000

				if blockType == 0x2000000:
					mat.addTexSlot(blockName, tex[params])

					log.log(f"{i}:{blockName}:{tex[params]}")
				else: # TODO: handle unknown block types
					setattr(mat, blockName, params)

					log.log(f"{i}:{blockName}:{params}")

			log.subLevel()

			mats.append(mat)

	def __getModelMaterials(self, model:str, blockNames:tuple[str]):
		tex = self.getModelTextures(model)

		if tex is None:
			raise Exception
		
		log.log(f"Pulling materials from GameResDesc for model {model}")
		log.addLevel()

		blk = self.__datablock.getByName(model)
		
		mats:list[MaterialData] = []

		for name in SafeIter(self, blockNames):
			self.__exploreMaterialBlock(blk, name, tex, mats)

		computeMaterialNames(mats, self)

		log.subLevel()

		return mats

	def getModelMaterials(self, model:str) -> list[MaterialData]:
		return self.__getModelMaterials(model, ("matR", "mat"))

	def getSkinnedMaterials(self, model:str):
		return self.__getModelMaterials(model, ("matS", ))
		

class GameResourcePack(Pack): # may need cleanup / TODO: rewrite like DXP2
	@classmethod
	@property
	def classNiceName(cls):
		return "Game Resource Pack"
	
	@classmethod
	@property
	def classIconName(self) -> str:
		return "folder_packed.bmp"
	
	@classmethod
	@property
	def fileExtension(self) -> str:
		return "grp"
	

	class RealResEntry: # TODO: parents do not work
		def __repr__(self):
			return f"<RealResEntry [{hex(self.__classId)}] #{self.__realResId} p={self.__parentCnt} @ {self.__offset}:{self.__name}>"
		
		def __init__(self, grp, idx:int, name:str, classId:int, offset:int, realResId:int, _resv:int):
			self.__grp:GameResourcePack = grp
			self.__classId = classId
			self.__offset = offset
			self.__realResId = realResId
			self.__resv = _resv
			self.__name = name

			self.__parentCnt = 0
			self.__pOffset = 0
			self.__parentRes:list[GameResourcePack.RealResEntry] = []

			if (idx != realResId):
				log.log(f"{self}: inconsistant idx={idx} realResId={realResId}", LOG_ERROR)

			# print(idx, hex(classId), offset, realResId, _resv)
		
		def getParentOffset(self):
			return self.__pOffset
		
		def getParentCnt(self):
			return self.__parentCnt
		
		def appendParentRes(self, parent):
			self.__parentRes.append(parent)
		
		def getParentRes(self):
			return self.__parentRes

		def getRealResData(self):
			log.log(f"Pulling {self.__name} from {self.__grp.name}")
			log.addLevel()
			resCnt = self.__grp.getRealResEntryCnt()

			size = 0

			if self.__realResId + 1 >= resCnt:
				size = self.__grp.size - self.__offset
			else:
				size = self.__grp.getRealResEntry(self.__realResId + 1).__offset - self.__offset
			
			resData:RealResData = None

			classId = self.__classId
			maskedClassId = classId & 0xFFFF0000

			if classId in REALRES_CLASSES_DICT:
				resData = REALRES_CLASSES_DICT[classId](self.__grp.filePath,
						   name = self.__name,
						   size = size, 
						   offset = self.__offset)
			elif maskedClassId in REALRES_CLASSES_DICT:
				resData = REALRES_CLASSES_DICT[maskedClassId](self.__grp.filePath,
						   name = self.__name,
						   size = size, 
						   offset = self.__offset)
			else:
				resData = UnknownResData(self.__grp.filePath, self.__name, size, self.__offset, self.__classId)
				log.log(f"Unknown resdata class {hex(self.__classId)}", LOG_WARN)

			resData.setCachedBinFile(self.__grp.cachedBinFile)

			log.subLevel()

			return resData
		
		def getName(self):
			return self.__name

		def appendData(self, classId:int, resId:int, resDataId:int, pOffset:int, parentCnt:int, l:int):
			# if resId != resDataId:
			# 	log.log(f"{self}: inconsistant resId={resId} resDataId={resDataId}", LOG_ERROR)
			
			self.__parentCnt = parentCnt
			self.__pOffset = pOffset

		
	def __init__(self, filePath:str, name:str = None):
		super().__init__(filePath, name)

		self.__readFile__()

		log.log(self)

		self._setValid()

	def __repr__(self):
		return f"<{self.name}.grp\tnmo={self.__nameMapOffset}\tnmn={self.__nameMapNum}\treo={self.__realResEntriesOffset}\trd2={self.__resData2}\trd2n={self.__resData2Num}>"

	def __readFile__(self):
		f = open(self.filePath, "rb")
		f.seek(0x8, 1)
		file = BinFile(f.read(readInt(f) + 0x4))
		f.close()

		OFS = 0xC

		# file.seek(0xC, 1)
		
		dataSize = readInt(file) + 0x10
		self._setSize(dataSize)

		nameMapOffset = readInt(file) - OFS
		nameMapNum = readInt(file)

		self.__nameMapOffset = nameMapOffset
		self.__nameMapNum = nameMapNum

		file.seek(8, 1)

		realResEntriesOffset = readInt(file) - OFS
		realResNum = readInt(file)

		self.__realResEntriesOffset = realResEntriesOffset

		file.seek(8, 1)

		resData2 = readInt(file) - OFS
		resData2Num = readInt(file)

		self.__resData2 = resData2
		self.__resData2Num = resData2Num

		file.seek(8, 1)
		
		nameMap = readNameMap(file, nameMapNum, nameMapOffset, 0x40, self)
		
		log.addLevel()
		
		self.__resEntriesOfs = file.tell()

		realResEntries = tuple(GameResourcePack.RealResEntry(self, i, nameMap[i],
								readInt(file), # classId
								readInt(file), # offset
								readShort(file), # realResId
								readShort(file)) # _resv
								for i in SafeRange(self, realResNum))
		
		# file.seek(0x10 - file.tell() % 0x10, 1)

		for i in SafeRange(self, resData2Num):
			realResEntries[i].appendData(readInt(file), # classId
								readShort(file), # resId
								readShort(file), # realResId
								readInt(file),
								readInt(file),
								readLong(file))

		# for resEntry in SafeIter(self, realResEntries):
			# file.seek(resEntry.getParentOffset(), 0)

			# for i in SafeRange(self, resEntry.getParentCnt()):
			# 	resEntryIdx = readShort(file)

			# 	try:
			# 		resEntry.appendParentRes(realResEntries[resEntryIdx])
			# 	except Exception as e: 
			# 		log.log(f"{e}: {i=} {resEntryIdx=}", LOG_ERROR)

		log.subLevel()

		# file.close()

		self.__realResEntries = realResEntries

	
	def getRealResEntry(self, resId:int):
		return self.__realResEntries[resId]
	
	def getRealResEntryCnt(self):
		return len(self.__realResEntries)
	
	def getRealResource(self, realResId:int):
		assert realResId >= 0 and realResId <= len(self.__realResEntries)

		return self.__realResEntries[realResId].getRealResData()
	
	def getPackedFiles(self):
		return tuple(self.getRealResource(i) for i in SafeRange(self, len(self.__realResEntries)))

	def getRealResId(self, name:str):
		for k, v in SafeEnumerate(self, self.__realResEntries):
			if v.getName() == name:
				return k

		raise ValueError(f"No such resource {name} in gameres {self}")

	def getResourceByName(self, name:str):
		return self.getRealResource(self.getRealResId(name))
	
	def getResEntryOffsets(self, realResId:int):
		ofs = self.__resEntriesOfs + realResId * 0xC
		return f"realResDataOfs1={ofs} realResDataOfs2={ofs + realResId * 0x18}"

class GameResourcePackBuilder:
	def __init__(self, name:str):
		self.__name = name
		self.__resources:list[RealResData] = []
	
	class ResEntry:
		def __init__(self, res:RealResData):
			self.__res = res

	def save(self, outpath:str = getcwd()):
		log.log(f"Generating Game Resource Pack {self.__name} ")
		log.addLevel()

		resCnt = len(self.__resources)

		idx = 0x40

		nameMap = b""
		nameMapIndices = BytesIO()

		log.log("Generating name map")
		for res in self.__resources:
			nameMapIndices.write(pack("I", idx))
			name = res.name.encode() + b"\0"
			nameMap += name
			idx += len(name)
		
		
		resDataSz = 0x18 * resCnt
		resEntriesSz = 0xC * resCnt
		dataStartOfs = 0x40 + len(nameMap) + nameMapIndices.tell() + resDataSz + resEntriesSz


		entries = BytesIO()
		resData = BBytesIO()
		rawData = BytesIO()

		log.log("Generating resource data")
		log.addLevel()
		for k, res in enumerate(self.__resources):
			curSz = dataStartOfs + rawData.tell() + 0x10

			entries.write(pack("2I2H", res.classId, curSz, k, 0))
			resData.write(pack("I2H2IQ", res.classId, k, k, 0, 0, 0))

			rawData.write(res.getBin().read())

			log.log(res)
		log.subLevel()

		fullSz = dataStartOfs + 0x10 + rawData.tell()

		b = BBytesIO()
		b.write(b"GRP2")
		b.write(pack("3I", dataStartOfs - 8, dataStartOfs - 4, fullSz - 0x10))
		b.write(pack("2IQ", 0x40 + len(nameMap), resCnt, 0))
		b.write(pack("2IQ", 0x40 + len(nameMap) + nameMapIndices.tell(), resCnt, 0))
		b.write(pack("2IQ", 0x40 + len(nameMap) + nameMapIndices.tell() + entries.tell(), resCnt, 0))
		b.write(nameMap)
		b.write(nameMapIndices.getvalue())
		b.write(entries.getvalue())
		b.write(resData.getvalue())
		b.write(bytes(0x10))
		b.write(rawData.getvalue())

		nameMapIndices.close()
		entries.close()
		resData.close()
		rawData.close()

		value = b.getvalue()
		b.close()

		filepath = path.join(outpath, self.name + ".grp")
		file = open(filepath, "wb")
		file.write(value)
		file.close()

		log.log(f"Wrote {len(value)} bytes to {filepath}")


	def append(self, res:RealResData):
		self.__resources.append(res)
	

	@property
	def name(self):
		return self.__name
