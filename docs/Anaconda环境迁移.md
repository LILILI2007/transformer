# Anaconda 项目环境

本项目使用 `D:\Anaconda3\envs\modlingo`，Python 3.9.25。运行和构建依赖安装在该环境内，项目以可编辑方式安装；源代码修改可以直接生效。旧 Miniconda 不参与项目启动、测试或打包。

## 日常使用

双击项目根目录 `Start.cmd`，自动进入项目的 Anaconda 环境。

也可以打开新的 PowerShell 或 Anaconda Prompt：

```powershell
conda activate modlingo
cd D:\英文mod翻译
python -m modlingo
```

VS Code 项目解释器设置为 `D:\Anaconda3\envs\modlingo\python.exe`。已打开的终端仍可能保留旧环境，迁移后请重新打开终端。

## 测试与构建

```powershell
.\scripts\test_windows.ps1
.\scripts\build_windows.ps1
```

上述脚本自行选择新环境，并清理当前进程继承的旧 Python、证书和 DLL 搜索路径。无需先激活环境；不会向实际游戏 Mods 或存档写入测试内容。

更换 Anaconda 安装位置时，设置 `MODLINGO_CONDA_ROOT`。测试和构建还支持 `MODLINGO_PYTHON` 或 `-PythonPath` 指定解释器。

## 重建环境

`environment.yml` 保存 Python、Tk 和依赖安装入口，`requirements-environment.lock.txt` 固定原构建环境中 16 个运行及打包依赖的版本，包括 `httpx==0.28.1` 和 `pyinstaller==6.16.0`。

```powershell
D:\Anaconda3\Scripts\conda.exe env create -f environment.yml
```

项目版本无需因解释器迁移改变。新打包的独立程序随包携带 Python 和依赖，运行它无需激活 Conda。

旧项目虚拟环境和终端初始化配置备份在工作区外的“ModLingo-环境迁移”目录，便于回溯。这里只迁移本项目；Miniconda 中其他命名环境不属于本次迁移范围。
