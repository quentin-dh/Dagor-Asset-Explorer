**NOTICE: This is a personal fork for testing and experimentation.**

I am currently investigating a fix for a rare Unicode/ASCII decoding bug that occurs when exporting models with Cyrillic characters in node names (e.g., `suspension_с_02`), which triggers a `UnicodeDecodeError` in `realres.py`.

This is a work in progress and not intended for production use.

# Dagor Asset Explorer

Dagor engine asset explorer and exporter coded in Python.

This is an old project of mine, yet it is still in an early state. A lot of features are missing.

To run the program, head to Releases -> Download the latest zip -> Run the exe

<div align="center">
    <img src="https://i.imgur.com/JiIftz6.png" alt="Screenshot of the main window in action" width="70%" />
</div>

You can either drag and drop files into the tree view or open an asset folder / asset files manually.


DAE was designed to fulfil a few purposes:
- Export static models without having to go through the hassle of using Ninja Ripper in the CDK
- Export assets from Enlisted
- Export models with skeletons
- Export map props easily
- Streamline model export to the Source engine (implemented for demo purposes!)

## Features

- Export models with bones to OBJ / DMF / Source engine
- Streamline model export directly to the Source engine
 - Dagor normal map format to Source normal map format conversion
 - Automatic conversion to VTF
 - Dagor shader parameters to VMT translation
 - QC generation with LOD support
 - Automatic split of submodels that exceed 11K vertices
 

## How to use:

If you do not know where the file you are looking for is located, just click *File -> Open asset folder...* and select the root directory of your game (E.g. `C:\...steamapps\common\War Thunder`). Then, just use the search bar to find the asset you are looking for.

Otherwise, you should follow these steps:
1. **Depending on the type of model you are looking for, open the according Game Res Desc: either `riDesc.bin` or `dynModelDesc.bin`**. If you are unsure, load both of them. In War Thunder, these files are located in `(gamedir)\content\base\res`.
If you do not load these, it is likely that your models will not have material group names.
2. **Load the GRP your model is located in**.
3. (Optional) **Load the necessary DDSx Pack (dxp.bin) files**
4. Find your model and export it to the format of your choice, although **it is heavily recommanded to export models as DMF**. Depending on your settings and the contents of the loaded DXPs, textures will be exported in the `textures` folder relative to your exported files.

## What's working:
- Exporting most textures (I used [klensy's wt-tools](https://github.com/klensy/wt-tools) as a base and added DXT10 support)
- Exporting most RendInst and DynModels
- Exporting map prop layout
- ~~Exporting sounds~~ (the program used to have FMod bank support, but I removed it after rewriting it and lacked the courage to add it back)
- Exporting models to Source MDL (SMD, QC, VMT, and VTF generation)

You should export all models to DMF as it is the only format with full skeleton and material support. Download the [Blender importer here](https://github.com/Gredwitch/Dagor-Asset-Explorer-Tools).

Models can be exported to OBJ without skeleton. An MTL file is automaticaly generated and textures are exported adequately (you can toggle this in the options).


## What's not working:
- Collision models are offset

## Map prop export demo

<div align="center">
    <a href="https://youtu.be/bHk0c9gkfYc"> <img src="https://i.imgur.com/h3vn0eS.png" alt="Youtube video: Dagor Asset Explorer - Per-cell prop export demo" width="70%" /> </a>
</div>

## Dagor formats-that-I-cover-more-or-less glossary
- **Game Resource Pack (*GRP*)**: file that contains Real Resource Data
- **Real Resource Data (*RRD*)**: they can be of several type, here are the ones I came across the most:
    - **Renderable instance (*RendInst*)**: static model, single mesh with textures, LODs and eventually an [imposter sprite](https://docs.unrealengine.com/4.27/en-US/RenderingAndGraphics/RenderToTextureTools/3/)
    - **Dynamic Renderable Scene (*DynModel*)**: model with a skeleton, several meshes and eventually skinned meshes
    - **GeomNodeTree**: skeleton, used with the according DynModel
    - **CollisionGeom**: collision model
    - **FX**: particle effect
    - **AnimChar**: animations
    - **PhysObj**: contains physics data such as mass, inertia tensor...
    - **LandClass**: used to automatically generate planted and tiled areas on a map as far as I can remember
    - **RandomGrass**: probably used to generate random vegetation
- **DDSx Pack (*DXP*)**: file that contains DDSx textures
- **DDSx**: compressed DDS texture
- **GameResDesc**: material and texture descriptors for models

## What to do if something breaks
1) Look in the issues if someone did not already report a similar issue
2) Make an issue, give me the full logs and the path to the resource you tried to export

## Compiling the GUI

```ps
PS C:\DagorAssetExplorer\src\dae> pyinstaller -p "./gui;./util;./parse" -F __main__.py -n DagorAssetExplorer
```

## Compiling dae_intrinsics

```ps
PS C:\DagorAssetExplorer\src\intrinsics> gcc -shared -Wall .\dae_intrinsics.c -o ..\bin\dae_intrinsics.dll
```


## Credits

- Quentin H.
- CloakerSmoker
