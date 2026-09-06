import sys
from os import path

sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))

import util.log as log
from ctypes import create_string_buffer, c_void_p, c_int64, c_int, c_size_t
from os import path
from zlib import decompress as zlibdecompress
from zlib import compress as zlibcompress
import lzma #use lzma
from util.misc import loadDLL
from struct import pack
from util.fileread import *

dakernel = loadDLL("daKernel-dev.dll")

if dakernel == None:
	print("Failed to load daKernel-dev.dll")
else:
	# size_t __fastcall oodle_compress(void *dst, unsigned __int64 maxDstSize, const void *src, unsigned __int64 srcSize, int compressionLevel)

	oodle_compress = dakernel[572]
	oodle_compress.argtypes = c_void_p, c_int64, c_void_p, c_int64, c_int
	oodle_compress.restype  = c_size_t

	# __int64 __fastcall oodle_decompress(void *dst, unsigned __int64 maxOriginalSize, const void *src, unsigned __int64 compressedSize)

	oodle_decompress = dakernel[574]
	oodle_decompress.argtypes = c_void_p, c_int64, c_void_p, c_int64
	oodle_decompress.restype  = c_int64

	# unsigned __int64 __fastcall zstd_compress(void *dst, unsigned __int64 maxDstSize, const void *src, unsigned __int64 srcSize, int compressionLevel)

	zstd_compress = dakernel[954]
	zstd_compress.argtypes = c_void_p, c_int64, c_void_p, c_int64, c_int
	zstd_compress.restype  = c_int64

	# unsigned __int64 __fastcall zstd_decompress(void *dst, unsigned __int64 maxOriginalSize, const void *src, unsigned __int64 srcSize)

	zstd_decompress = dakernel[962]
	zstd_decompress.argtypes = c_void_p, c_int64, c_void_p, c_int64
	zstd_decompress.restype  = c_int64


class CompressedData:
	def __init__(self, file:BinFile, cMethod:int = None):
		if cMethod != None:
			self.cSz = len(file)
			self.cMethod = cMethod
			self.cData = file
		else:
			self.cSz = readEx(3, file)
			self.cMethod = readByte(file)
			self.cData = file.read(self.cSz)
	
	def decompress(self, outName:str = None):
		data = None

		if self.cMethod == 0x40:
			data = zstdDecompress(self.cData)
		elif self.cMethod == 0x60:
			data = zlibDecompress(self.cData)
		elif self.cMethod == 0x20:
			data = lzmaDecompress(self.cData)
		elif self.cMethod == 0x80:
			data = oodleDecompress(self.cData)
		else:
			log.log(f"Unknown compression method {hex(self.cMethod)}", log.LOG_ERROR)
			
		if outName is not None and not path.exists(outName):
			if data != None:
				cFile = open(outName, "wb")
				cFile.write(data)
				cFile.close()
			
				log.log(f"Wrote {len(data)} bytes to {outName}")
		
		return data
	
	def decompressToBin(self):
		d = self.decompress()
		
		if d is None:
			return None
		else:
			return BinFile(d)

def compressBlock(data:bytes, cMethod:int, level:int = None):
	if cMethod == 0x40:
		if level:
			cData = zstdCompress(data, level)
		else:
			cData = zstdCompress(data)
	elif cMethod == 0x60:
		cData = zlibCompress(data)
	elif cMethod == 0x20:
		cData = lzmaCompress(data)
	elif cMethod == 0x80:
		if level:
			cData = oodleCompress(data, level)
		else:
			cData = oodleCompress(data)
	else:
		raise ValueError(f"Unknown compression method {hex(cMethod)}")
	
	return pack("<L", len(cData))[:3] + pack("<B", cMethod)[:1] + cData


def oodleDecompress(src:bytes, maxOriginalSize:int = None):
	if not maxOriginalSize:
		maxOriginalSize = toInt(src[:4])
		src = src[4:]
	
	compressedSize = len(src)
	dst = create_string_buffer(maxOriginalSize)

	result = oodle_decompress(dst, maxOriginalSize, src, compressedSize)

	if result == 0:
		raise Exception(f"Oodle error")
	
	return dst.raw

def oodleCompress(src:bytes, compressionLevel:int = 0x4):
	# enum oo2::OodleLZ_CompressionLevel, copyof_254, signed, width 4 bytes
	# 	OodleLZ_CompressionLevel_None  = 0
	# 	OodleLZ_CompressionLevel_SuperFast  = 1
	# 	OodleLZ_CompressionLevel_VeryFast  = 2
	# 	OodleLZ_CompressionLevel_Fast  = 3
	# 	OodleLZ_CompressionLevel_Normal  = 4
	# 	OodleLZ_CompressionLevel_Optimal1  = 5
	# 	OodleLZ_CompressionLevel_Optimal2  = 6
	# 	OodleLZ_CompressionLevel_Optimal  = 6
	# 	OodleLZ_CompressionLevel_Optimal3  = 7
	# 	OodleLZ_CompressionLevel_Optimal4  = 8
	# 	OodleLZ_CompressionLevel_Optimal5  = 9
	# 	OodleLZ_CompressionLevel_Max  = 9
	# 	OodleLZ_CompressionLevel_Count  = 0Ah
	# 	OodleLZ_CompressionLevel_Force32  = 40000000h
	# 	OodleLZ_CompressionLevel_Invalid  = 40000000h
	# 	OodleLZ_CompressionLevel_HyperFast4  = -4
	# 	OodleLZ_CompressionLevel_Min  = -4
	# 	OodleLZ_CompressionLevel_HyperFast3  = -3
	# 	OodleLZ_CompressionLevel_HyperFast2  = -2
	# 	OodleLZ_CompressionLevel_HyperFast1  = -1
	# 	OodleLZ_CompressionLevel_HyperFast  = -1

	# NOTE : compressor = (compressionLevel / 10 > 0) ? OodleLZ_Compressor_Leviathan : OodleLZ_Compressor_Kraken
	# NOTE : OodleLZLevel = compressionLevel % 10 (actual compression level)

	srcSize = len(src)
	maxDstSize = srcSize + 274 * ((srcSize + 0x3FFFF) // 0x40000) # OodleLZ_GetCompressedBufferSizeNeeded(srcSize)
	dst = create_string_buffer(maxDstSize)

	result = oodle_compress(dst, maxDstSize, src, srcSize, compressionLevel)
	
	if result == 0:
		raise Exception(f"Oodle error")
	
	return pack("I", srcSize) + dst.raw[:result]

def zstdDecompressTest(src:bytes, maxOriginalSize:int = None):
	if not maxOriginalSize:
		maxOriginalSize = toInt(src[5:8])
	
	compressedSize = len(src)
	dst = create_string_buffer(maxOriginalSize)

	result = zstd_decompress(dst, maxOriginalSize, src, compressedSize)

	if result == 0:
		raise Exception(f"ZStandard error")
	return dst.raw

def zstdCompress(src:bytes, compressionLevel:int = 18):
	srcSize = len(src)
	maxDstSize = srcSize
	dst = create_string_buffer(maxDstSize)

	result = zstd_compress(dst, maxDstSize, src, srcSize, compressionLevel)
	
	if result == 0:
		raise Exception(f"ZStandard error")
	
	return dst.raw[:result]

from zstandard import ZstdDecompressor

def zstdDecompress(src:bytes, maxOriginalSize:int = None):
	return ZstdDecompressor().decompress(src)


def zlibDecompress(data:bytes):
	return zlibdecompress(data)

def zlibCompress(data:bytes):
	return zlibcompress(data)

def lzmaDecompress(data:bytes):
    return lzma.decompress(data)

def lzmaCompress(data:bytes):
    return lzma.compress(data)
#use lzma