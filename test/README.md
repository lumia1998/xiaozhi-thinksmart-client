# 客户端测试

在 Ubuntu 带屏音箱或 Ubuntu 测试环境运行，使用 Python 3.12、系统 `libspeexdsp1` 和 `libopus0`。页面逻辑测试需要 Node.js。

在项目目录执行以下命令。

```sh
python3 -m venv test/.venv
test/.venv/bin/python -m pip install -r test/requirements.txt
PYTHONDONTWRITEBYTECODE=1 test/.venv/bin/python -m pytest -c test/pytest.ini -q
```

测试替换声卡、唤醒词模型和 WebRTC APM 的设备接口，Speex 回声消除使用真实系统库。配置文件和屏幕状态文件写到测试临时目录。

测试环境位于 `test/.venv/`，缓存位于 `test/.pytest_cache/`，本地日志、报告和辅助工具放在 `test/.local/`，这些目录由 Git 忽略。
