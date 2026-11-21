from version import VERSION
from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo,
    StringTable, StringStruct, VarFileInfo, VarStruct)
t = (tuple(int(x) for x in VERSION.split(".")) + (0, 0, 0, 0))[:4]  # Windows wants exactly 4 numbers
info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=t, prodvers=t, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "mindzy007"), StringStruct("FileDescription", "Kernel"),
        StringStruct("FileVersion", VERSION), StringStruct("InternalName", "Kernel"),
        StringStruct("OriginalFilename", "Kernel.exe"), StringStruct("ProductName", "Kernel"),
        StringStruct("ProductVersion", VERSION)])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])])])
open("version_info.txt", "w", encoding="utf-8").write(str(info))
