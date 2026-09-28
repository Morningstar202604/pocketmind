# 离线语音（可选能力）

口袋智灵 支持**完全离线**的中文语音朗读：不依赖网络、不依赖 termux-api，
语音在本机由 sherpa-onnx 引擎合成。**不装也不影响主功能**，属可选项。

## 安装（两步，约 135MB）

```bash
# 1) 安装 sherpa-onnx（Python 引擎，wheel 约 4MB）
pip install sherpa-onnx

# 2) 下载中文语音模型（约 130MB，放入数据目录的 tts-model/）
cd $HOME/.agent/termux-agent          # 与 config.json 同目录
curl -L -o tts.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-vits-zh-ll.tar.bz2
tar xjf tts.tar.bz2
mv sherpa-onnx-vits-zh-ll tts-model   # 目录名必须是 tts-model
rm tts.tar.bz2
```

## 效果

装好后，「手机能力」清单会出现第 31 个工具 `tts_offline`（本地离线语音朗读）。
对 Agent 说「用离线语音念一遍这句话」，会合成 wav 到 `$AGENT_HOME/cache/`，
手机端可直接播放（Termux 里可用 `termux-open` 播放，或通过文件工具访问路径）。

引擎：sherpa-onnx vits-zh（k2-fsa 开源项目，Apache-2.0，模型 5 个内置音色）。

## 常见问题

- **工具报「未安装 sherpa-onnx」**：执行 `pip install sherpa-onnx` 后重启 agentd。
- **工具报「未找到离线模型」**：模型没放进 `$AGENT_HOME/tts-model/`（必须含
  `model.onnx` 与 `tokens.txt`），按上面两步下载。
- **想更小的模型**：可用 sherpa-onnx 的其他中文 vits 模型替换 tts-model 内容，
  目录结构保持一致即可。
