# 发音人 (vcn) 与选型

## 1. 文档附录中的发音人

| 中文名称 | vcn | 音色 | 语种/方言 | 风格 |
| --- | --- | --- | --- | --- |
| 希涵 | `x4_yeting` | 女 | 中文/普通话 | 游戏影视解说 |
| 关山-专题 | `x4_guanshan` | 男 | 中文/普通话 | 专题片纪录片 |
| 小鹏 | `x4_pengfei` | 男 | 中文/普通话 | 新闻播报 |
| 千雪 | `x4_qianxue` | 女 | 中文/普通话 | 阅读听书 |
| 聆伯松-老年男声 | `x4_lingbosong` | 男 | 中文/普通话 | 阅读听书 |
| 秀英-老年女声 | `x4_xiuying` | 女 | 中文/普通话 | 阅读听书 |
| 明哥 | `x4_mingge` | 男 | 中文/普通话 | 阅读听书 |
| 豆豆 | `x4_doudou` | 男 | 中文/男童 | 阅读听书 |
| 聆小珊 | `x4_lingxiaoshan_profnews` | 女 | 中文/普通话 | 新闻播报 |
| 小果 | `x4_xiaoguo` | 女 | 中文/普通话 | 新闻播报 |
| 小忠 | `x4_xiaozhong` | 男 | 中文/普通话 | 新闻播报 |
| 小露 | `x4_yezi` | 女 | 中文/普通话 | 通用场景 |
| 超哥 | `x4_chaoge` | 男 | 中文/普通话 | 新闻播报 |
| 飞碟哥 | `x4_feidie` | 男 | 中文/普通话 | 游戏影视解说 |
| 聆飞皓-广告 | `x4_lingfeihao_upbeatads` | 男 | 中文/普通话 | 直播广告 |
| 嘉欣 | `x4_wangqianqian` | 女 | 中文/普通话 | 直播广告 |
| 聆小臻 | `x4_lingxiaozhen_eclives` | 女 | 中文/普通话 | 直播广告 |

## 2. 服务端实际接受的完整 vcn 列表

以下列表来自 `dts_create` 返回的 `10163` schema 校验错误（即服务端权威的允许集合），共 25 个。
其中 8 个未出现在文档附录里，官方元数据（音色/风格）未公开，已按 vcn 命名推断用途标注为“未公开”。

```
x3_xiaoyue
x4_EnUs_Catherine_profnews      # 英文女声，新闻
x5_lingfeizhe                   # 未公开
x5_lingxiaoxue                  # 未公开
x4_lingfeihong_document         # 未公开（document = 文档朗读）
x4_lingfeichen_assist           # 未公开（assist = 助手）
x4_pengfei
x4_yeting
x4_qianxue
x4_guanshan
x4_lingxiaoqi_assist            # 未公开（assist = 助手）
x4_lingfeihong_document_n       # 未公开
x4_lingbosong
x4_xiuying
x4_mingge
x4_doudou
x4_lingxiaoshan_profnews
x4_xiaoguo
x4_xiaozhong
x4_yezi
x4_chaoge
x4_feidie
x4_lingfeihao_upbeatads
x4_wangqianqian
x4_lingxiaozhen_eclives
```

> 该列表是**平台允许集合**，不代表你账号已开通。实际可用性还取决于控制台是否为该应用开通了对应发音人。
> 若某个 vcn 调用返回 `11200 licc limit`，通常是**该发音人未开通**或**服务额度用尽**，而不是参数错误。

## 3. 选型建议

| 场景 | 推荐 vcn |
| --- | --- |
| 长篇小说 / 听书 | `x4_qianxue`、`x4_mingge`、`x4_lingbosong`、`x4_xiuying` |
| 新闻播报 / 资讯 | `x4_pengfei`、`x4_lingxiaoshan_profnews`、`x4_xiaoguo`、`x4_xiaozhong`、`x4_chaoge` |
| 专题片 / 纪录片旁白 | `x4_guanshan` |
| 游戏/影视解说 | `x4_yeting`、`x4_feidie` |
| 广告 / 直播带货 | `x4_lingfeihao_upbeatads`、`x4_wangqianqian`、`x4_lingxiaozhen_eclives` |
| 通用默认 | `x4_yezi`、`x4_mingge` |
| 童声 | `x4_doudou` |
| 英文内容 | `x4_EnUs_Catherine_profnews` + `--language en` |

不确定时先跑一个小样本对比：

```bash
SKILL=<本 skill 目录>          # 即该 skill 的 SKILL.md 所在目录
for v in x4_mingge x4_qianxue x4_pengfei x4_yezi; do
  python3 "$SKILL/scripts/xfyun_tts.py" synth -t "这是${v}的试听样本。" -v "$v" -o "sample_${v}.mp3" -q
done
```

## 4. 查询当前可用列表

```bash
cd <xfyun-long-tts skill 目录> && python3 scripts/xfyun_tts.py voices
```

若想拿服务端当下的权威列表，可故意发送一个非法 vcn，从 `10163` 的报错里读取：

```bash
python3 - <<'PY'
import json,subprocess
out=subprocess.run(["python3","scripts/xfyun_tts.py",   # 在本 skill 目录下运行
                    "synth","-t","x","-v","x3_mingge","-q"],capture_output=True,text=True)
print(json.loads(out.stdout).get("error"))
PY
```
