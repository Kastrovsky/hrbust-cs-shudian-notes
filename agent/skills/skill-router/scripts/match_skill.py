#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按用户原话匹配最合适的技能。

用法：
    python match_skill.py "把第5章讲义改成手写笔记风格"
    python match_skill.py --list            # 只打印路由表，不做匹配
    python match_skill.py --top 5 "..."     # 返回前 5 个候选

原理：
    1. 从 SKILL.md 的路由表里解析「触发特征 | 技能 | 注意」三列；
    2. 把「触发特征」按 / 、 ， 空格 切成短语，作为该技能的关键词；
    3. 关键词出现在用户原话里即得分，越长权重越高（长词更具体，误判更少）；
    4. 全部为 0 时退化为「与技能 description 的二字片段重合度」，只用于提示弱相关；
    5. 得分低于阈值就明确说"无匹配"，避免硬套技能。

为什么用脚本而不是让我凭记忆翻表：
    技能会随安装/删除而变化，记忆里的表会过期；脚本每次都读当前 SKILL.md，
    并且打分过程可复现、可回归测试，减少"漏看一行"造成的错配。
"""

import os
import re
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
INDEX_MD = os.path.join(SKILL_DIR, "references", "skill-index.md")

# 得分阈值：低于此值视作"无匹配"，宁可走通用能力也不要硬套
THRESHOLD = 4
# 非元技能的强命中门槛：用户已明确指向某个具体工作流时，
# 元技能兜底不再抢 top-1（详见 score() 末尾）
STRONG_HIT = 30


def read_text(path):
    """读文件，统一换行；失败返回空串。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read().replace("\r\n", "\n").replace("\r", "\n")
    except OSError:
        return ""


def parse_triggers():
    """解析「## 触发词（机器匹配用）」小节，返回 {技能: [关键词]}。

    这一节是给机器看的短词表，比路由表里的整句话更适合做匹配；
    没在这一节登记的技能会自动退回用路由表的「触发特征」列。
    """
    text = read_text(SKILL_MD)
    start = text.find("## 触发词")
    if start < 0:
        return {}
    end = text.find("\n## ", start + 3)
    section = text[start:end] if end > start else text[start:]

    out = {}
    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        m = re.search(r"`([A-Za-z0-9_\-\.]+)`", cells[0])
        if not m:
            continue
        kws = [k.strip(" `") for k in re.split(r"[,，;；]", cells[1])]
        kws = [k for k in kws if len(k) >= 2]
        if kws:
            out[m.group(1)] = kws
    return out


def parse_routes():
    """解析路由表，返回 [{kw:[...], skill:str, note:str, raw:str}]。

    只取「## 路由表」到「## 组合规则」之间的表格行，其余小节不参与匹配。
    """
    text = read_text(SKILL_MD)
    start = text.find("## 路由表")
    end = text.find("## 组合规则")
    if start < 0:
        return []
    section = text[start:end] if end > start else text[start:]

    routes = []
    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # 两列表（产物 | 加载顺序）与三列表（特征 | 技能 | 注意）都要吃
        if len(cells) < 2:
            continue
        # 表头与分隔行跳过
        if set(cells[1]) <= set("-: "):
            continue
        # 技能名优先取第二列，两列表可能把多个技能挤在一格里
        names = re.findall(r"`([A-Za-z0-9_\-\.]+)`", cells[1])
        if not names:
            names = re.findall(r"`([A-Za-z0-9_\-\.]+)`", line)
        if not names:
            continue
        note = cells[2] if len(cells) >= 3 else ""
        kws = split_keywords(cells[0])
        if not kws:
            continue
        # 一行里可能出现多个技能（A / B），拆开各生成一条
        for name in names:
            routes.append({"kw": kws, "skill": name, "note": note, "raw": cells[0]})
    return routes


def split_keywords(cell):
    """把「触发特征」拆成短语列表，丢弃过短的噪声词。"""
    # 去掉"（用户的说法）"这类说明性括号内容与引号
    cell = re.sub(r"[（(][^）)]*[）)]", " ", cell)
    cell = cell.replace("`", " ").replace("“", " ").replace("”", " ")
    parts = re.split(r"[/、，,；;。\s]+", cell)
    out = []
    for p in parts:
        p = p.strip(" …—-")
        if len(p) >= 2:
            out.append(p)
    return out


def bigrams(text):
    """中文按二字片段切分，用于弱相关兜底。"""
    text = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text)
    return {text[i : i + 2] for i in range(len(text) - 1)}


def lcs_len(a, b):
    """最长公共子串长度（连续），用于包容口语化改写。"""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def load_desc():
    """从 skill-index.md 取技能 description，供兜底打分。"""
    desc = {}
    for line in read_text(INDEX_MD).split("\n"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.search(r"`([A-Za-z0-9_\-\.]+)`", cells[1])
        if m:
            desc[m.group(1)] = cells[2]
    return desc


# 元技能兜底用词表。谈"技能机制本身"必须同时具备名词 + 动作，
# 再叠加"判断语气"或"机制类话题"，才能与"帮我建个新技能"（执行型命令）区分开。
META_NOUNS = ("技能", "skill")
META_ACTS = ("沉淀", "固化", "建", "更新", "匹配", "做成", "梳理", "统一")
META_JUDGE = ("要不要", "该不该", "值得", "有必要", "是否", "吗", "?", "？")
META_TOPIC = ("沉淀", "固化", "匹配机制", "匹配")


def _looks_like_meta(query):
    """判断原话是不是在谈"技能机制本身"（该不该建、怎么改匹配）。

    字符串匹配接不住中文长句："更新一下技能匹配机制"里"更新匹配机制"并不连续，
    按关键词打分只会命中 skill-creator 的模糊词，给出错误建议。这里用语义规则补位。

    "帮我建个新技能"不算——它有名词有动作，但没有判断语气、也不谈机制，
    属于执行型命令，应当继续交给 skill-creator。
    """
    q = query.lower()
    if not any(n in q for n in META_NOUNS):
        return False
    if not any(a in q for a in META_ACTS):
        return False
    return any(j in q for j in META_JUDGE) or any(t in q for t in META_TOPIC)


def score(query, routes, desc):
    """给每个技能打分，返回按分数降序的 [(skill, score, hits, note)]。

    两档计分：
    - 完整命中：触发短语原样出现在原话里，权重按长度平方增长（长词更具体）；
    - 部分命中：触发短语与"原话中某段"的最长公共子串占比 ≥ 0.6，
      用来接住"补一下这章的公式"（命中词是"公式漏"）这类口语改写。

    为什么要 0.6 而不是 0.5：中文长句里两字碎片几乎必然出现，
    0.5 门槛下"仓库操作"能靠"仓库"二字给 github 记 2 分、
    "生成图片"靠"图片"给 ImageGen 记 2 分，多条碎片一累加就越过阈值抢走 top-1。
    实测把课程仓库任务误判成 github / pptx / ImageGen 全是这么来的。

    同一技能的同一个词只计一次：路由表里一个技能可能出现在多行（如 PPT 在两节都登记），
    不去重会把同一份证据算两三遍。
    """
    q_bi = bigrams(query)
    table = {}
    for r in routes:
        item = table.setdefault(
            r["skill"], {"score": 0, "hits": [], "note": r["note"]}
        )
        for kw in r["kw"]:
            if kw in item["hits"] or (kw + "~") in item["hits"]:
                continue  # 同一证据不重复计分
            if kw in query:
                # 长关键词更具体，权重按长度平方增长；短词（2 字）也至少给 4 分，
                # 否则"蒸馏""公式"这类有效短词会被阈值挡掉
                item["score"] += len(kw) ** 2 // 2 + 2
                if kw not in item["hits"]:
                    item["hits"].append(kw)
                continue
            if len(kw) < 2:
                continue
            hit_len = lcs_len(kw, query)
            # 至少连续命中 2 个字才算部分匹配，否则"文献"撞上"文"也会算分
            if hit_len < 2:
                continue
            ratio = hit_len / len(kw)
            if ratio >= 0.6:
                item["score"] += 1 + int(2 * ratio)
                tag = kw + "~"
                if tag not in item["hits"]:
                    item["hits"].append(tag)

    # 兜底：**所有技能的关键词都没命中**时，才用 description 的字面重合度给弱提示。
    # 2026-11-15 修：原实现是"每个技能各自判断 score==0"，于是没有任何触发词的技能
    # 也能靠 description 拿分，且可能反超真正命中触发词的技能——
    # "把这个网站内该课程的作业单独做一个 Markdown 文件…" 被判给 math-formula-audit，
    # 命中理由竟是 "Ma、ar、do、kd"（Markdown 的英文二字片段）。两处收紧：
    #   ① 只有全局零命中才启用；② 只用汉字二字片段，英文/数字片段一律不算证据。
    if all(info["score"] == 0 for info in table.values()):
        for skill, info in table.items():
            overlap = {b for b in (q_bi & bigrams(desc.get(skill, "")))
                       if re.match(r"^[\u4e00-\u9fa5]{2}$", b)}
            if len(overlap) >= 3:
                info["score"] = len(overlap) // 2
                info["hits"] = ["（描述相似：%s）" % "、".join(sorted(overlap)[:4])]

    # 元技能兜底：判定"要不要沉淀/怎么改匹配"时，必须让 skill-router 进候选，
    # 否则会被 skill-creator 的模糊词抢走，直接开建而跳过三问过滤。
    # 分数要压过当前最高分——打平会按字母序排，skill-creator 会排在前面。
    # 但用户若已明确指向某个具体工作流（非元技能得分 >= STRONG_HIT），
    # 说明这不是"技能机制"话题，只是长句里顺带提到了 skill/匹配，兜底必须让位，
    # 否则专用技能永远被顶到第二，等于匹配器失效。
    specialist_top = max(
        (i["score"] for s, i in table.items() if s != "skill-router"), default=0
    )
    if (_looks_like_meta(query) and "skill-router" in table
            and specialist_top < STRONG_HIT):
        item = table["skill-router"]
        top = max((i["score"] for i in table.values()), default=0)
        item["score"] = max(item["score"], top + 2, THRESHOLD + 8)
        if "（元技能兜底）" not in item["hits"]:
            item["hits"].append("（元技能兜底）")

    rows = [
        (s, i["score"], i["hits"], i["note"]) for s, i in table.items() if i["score"] > 0
    ]
    rows.sort(key=lambda x: (-x[1], x[0]))
    return rows


def print_table(routes):
    """按技能去重打印路由表，便于人工核对。"""
    seen = []
    for r in routes:
        if r["skill"] in [s["skill"] for s in seen]:
            continue
        seen.append(r)
    for r in seen:
        print("{:<28} {}".format(r["skill"], "、".join(r["kw"])[:90]))


SELFTEST_CASES = [
    ("把第5章讲义改成手写笔记风格", "handwritten-study-notes"),
    ("这章笔记太抽象了，全是堆定义", "handwritten-study-notes"),
    ("补一下这章的公式，感觉漏了很多", "math-formula-audit"),
    ("把这个考点在练习题和真题里的第几题标出来", "question-bank-crossref"),
    ("检查一下笔记里引的题号是不是都真有这道题", "question-bank-crossref"),
    ("这些配图太糊了，换成课件原图", "course-figure-pipeline"),
    ("图片太大了，压一下顺便限宽", "course-figure-pipeline"),
    ("把这周的雅思口语课材料整理一下", "ielts-class-notes"),
    ("用这些单词给我出一套口语练习", "ielts-wordbank-drill"),
    ("把这本书蒸馏成精华", "book-distillation"),
    ("帮我找几篇RAG医疗的论文", "global-biblio-base"),
    ("参考文献整理成 GB/T 7714 格式", "paper-reference-formatter"),
    ("给这个仓库提个PR", "github"),
    ("做一份PPT讲讲第三章", "workbuddy-pptx"),
    ("整门课的教材按章节整理成知识点体系", "course-study-kit"),
    ("以后都按这个标准来，把流程固化下来", "skill-router"),
    ("这段经验要不要沉淀成技能", "skill-router"),
    # 以下三条钉住"判定 vs 执行"的分工边界，以及中文长句的元技能兜底
    ("这种活儿以后还会遇到，值得做成一个技能吗", "skill-router"),
    ("更新一下技能匹配机制", "skill-router"),
    ("帮我建个新技能", "skill-creator"),
    # ---- 以下钉住"中文长句里的泛词蹭分"这个真实翻车点（2026-11-12 现场抓到）----
    # 正例：章节内容的补充，就该走手写笔记体例
    ("把课堂照片里的关键例题补充到章节里", "handwritten-study-notes"),
    ("从幻灯片提取关键图表并并入讲义", "handwritten-study-notes"),
    # 2026-11-12 现场翻车：用户只发了一张板书照片，我把"讲解这道题"当成任务去匹配
    # → 无匹配 → 回了一段聊天解答，没落 md 没推送。正确做法是按输入物+产物匹配。
    ("黑板上这道题整理进讲义并推送", "handwritten-study-notes"),
    ("板书上的例题补进第6章", "handwritten-study-notes"),
    # 负例（"!技能名" = 断言不许判成它）：
    # "对仓库进行补充"里的"仓库"是课程资料仓库，不是 GitHub
    ("提取关键的内容，关键例题，对仓库进行补充", "!github"),
    # "图片信息及其最短路径的算法"里的"图片"是投影照片，不是要生成图
    ("图片信息及其最短路径的算法，例如迪杰斯特拉等等", "!ImageGen"),
    # 抽检补的第 25 例：仓库里的图片处理归配图管线，不归 GitHub
    ("把这个数据结构仓库的图片压一下", "course-figure-pipeline"),
    # 临时产物清理：用户原话 + 两种常见改写
    ("确保没用的临时产物每次都能正确地删除", "tmp-hygiene"),
    ("任务做完了，把中间文件删干净", "tmp-hygiene"),
    ("这些临时文件还要吗，不要就清掉", "tmp-hygiene"),
    # 2026-11-12 雅思素材库任务：长句里同时出现 skill / GitHub / 单词，
    # 元技能兜底会让位给强命中的专用技能（STRONG_HIT），不许泛词抢走 top-1
    ("针对用户发送的文件、文本内容，提取雅思重点词汇、重点短语、俚语，配套雅思英汉双释义、例句、用法，区分标注口语用法、写作用法；按数量拆分归档词汇短语素材文件，范文按雅思题型分类，每周定时任务每周日执行素材提取，后续推送至 Gitee、GitHub。每一次执行任意任务前，必须先进行 skill 检索匹配。", "ielts-material-bank"),
    ("用素材总索引里已入库的词条写一篇雅思范文，标注官方范文并按雅思题型分类归档", "ielts-material-bank"),
    ("针对用户发送的文件提取雅思重点词汇短语，配英汉双释义并区分标注口语用法、写作用法，支撑每周定时任务撰写雅思范文，后续推送至 GitHub", "!github"),
    # 2026-11-15 超星作业归档任务：用户原话里同时有"网站""Markdown""答案""解析""知识点""过程"，
    # 会被 handwritten-study-notes / course-study-kit / question-bank-crossref 分走；
    # 判据是输入物形态（网课平台里的作业）＋产物（每份作业一个 md），不是"我打算怎么答"
    ("把这个网站内该课程的作业单独创建一个文件夹，并把每一个作业单独做一个 Markdown 文件，题目源自网站上的原题，把答案、解析以及涉及的知识点和过程都包含进去", "chaoxing-homework-archive"),
    ("超星学习通上的网课作业整理成 md，每份作业一个文件", "chaoxing-homework-archive"),
    # 同一句话里带"超星"但诉求是判卷结果回顾 → 仍是本技能（归档时写错因），不许被判成题号对照
    ("学习通里已完成的那几份章节测试，把判错的题和错因一起整理进作业仓库", "chaoxing-homework-archive"),
    # 2026-11-15 触发本次事故的用户原话：课程链接 + 每份作业一个 Markdown + 原题/答案/解析/知识点/过程
    ("把这个网站内该课程的作业单独创建一个文件夹，并把每一个作业单独做一个 Markdown 文件，题目要求源自网站上的原题，内容把答案、解析以及涉及的知识点和过程都包含进去", "chaoxing-homework-archive"),
    ("课程归属回验一下，是不是抓错课程了，courseid 对不上", "chaoxing-homework-archive"),
    ("课程归属回验一下，是不是抓错课程了，courseid 对不上", "!math-formula-audit"),
    # 2026-11-15 巡检新建 concurrent-write-guard：多会话并发写同一批文件时的取证与收尾核三端。
    # 触发场景来自 11-10 / 11-12 四条真实事故（文件被覆盖、同一节出现三份、三端不一致、.ssh 时拦时通），
    # 下列为用户侧会出现的典型说法（原日志里是任务执行记录，非逐字原话）。
    ("这个文件怎么自己变了，我刚才写的一段没了", "concurrent-write-guard"),
    ("我开了两个窗口改同一个仓库，提交前先核对一下有没有并发提交、三端是不是一致", "concurrent-write-guard"),
    ("先把这批文件探一下稳不稳定，别再被我另一个会话覆盖了", "concurrent-write-guard"),
    # 2026-11-15 巡检新建 deliverable-reconcile：文档与真实产物对账（输出块 / 链接 / 只增不删）。
    ("确保文档里的程序输出是真实跑出来的，不是手抄的", "deliverable-reconcile"),
    ("作业里的图片链接自检一下，压缩之后是不是断链了", "deliverable-reconcile"),
    ("配套程序只增不删，编译必须零警告，旧的演示输出不能变", "deliverable-reconcile"),
    # 负例：这条是笔记体例的活，不该被"文档自检"抢走
    ("把这章笔记改成手写笔记风格，压到能背下来", "!deliverable-reconcile"),
    # 2026-11-22 巡检新建 prepush-secret-gate：推送前机械凭据闸门（11-15 用户定红线，11-15/17/19 三仓库复现）。
    # 第一条是 11-15 数电的用户原话。
    ("上传到仓库之前，必须检查有没有把密码、账号什么的包含进去，绝对不能包含进去", "prepush-secret-gate"),
    ("给这个仓库装个 pre-push 钩子，提交前自动查有没有手机号、令牌、私钥", "prepush-secret-gate"),
    ("凭据闸门误报了，构建产物里的 base64 字体被当成令牌", "prepush-secret-gate"),
    # 负例：讲的是"推送不通"，不是"凭据"，别抢 concurrent-write-guard 的活
    ("推送报 502 代理隧道失败，gitee 通了 github 不通怎么办", "!prepush-secret-gate"),
]


def selftest(routes, desc):
    """回归测试：断言 top-1 命中预期技能。路由表改动后必跑。

    期望值以 "!" 开头表示**否定断言**：top-1 不许是该技能。
    用来钉住"泛词蹭分"这类翻车——中文长句里"仓库""图片""操作"几乎必然出现，
    单靠正例只能证明"该中的中了"，证不了"不该中的没中"。
    """
    bad = 0
    for query, expect in SELFTEST_CASES:
        rows = score(query, routes, desc)
        got = rows[0][0] if rows and rows[0][1] >= THRESHOLD else "（无）"
        if expect.startswith("!"):
            ok = got != expect[1:]
            expect_txt = "≠%s" % expect[1:]
        else:
            ok = got == expect
            expect_txt = expect
        bad += 0 if ok else 1
        print("{}  {:<34} -> {:<26} 期望 {}".format(
            "PASS" if ok else "FAIL", query, got, expect_txt))
    print("-" * 60)
    print("共 {} 例，失败 {} 例".format(len(SELFTEST_CASES), bad))
    return 0 if bad == 0 else 1


def main():
    argv = sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    routes = parse_routes()
    if not routes:
        print("未能从 SKILL.md 解析出路由表：", SKILL_MD)
        return 1

    # 触发词小节优先：那里是短词，比整句更适合匹配
    triggers = parse_triggers()
    for r in routes:
        if r["skill"] in triggers:
            r["kw"] = triggers[r["skill"]]

    if "--selftest" in argv:
        return selftest(routes, load_desc())

    if "--list" in argv:
        print("路由表技能数：", len({r["skill"] for r in routes}))
        print("触发词已登记：", len(triggers))
        print_table(routes)
        return 0

    # 先剥掉 --top N，再判断是否只剩选项——否则 "--top 3 \"原话\"" 会被误判为用法错误
    top = 3
    if "--top" in argv:
        i = argv.index("--top")
        try:
            top = int(argv[i + 1])
        except (IndexError, ValueError):
            top = 3
        argv = argv[:i] + argv[i + 2 :]

    if not argv or argv[0].startswith("--"):
        print('用法：python match_skill.py "用户的原话"')
        return 1

    query = " ".join(argv).strip()
    rows = score(query, routes, load_desc())

    print("查询：", query)
    print("-" * 60)
    if not rows or rows[0][1] < THRESHOLD:
        if rows:
            print("弱相关（未达阈值 %d，仅供参考）：" % THRESHOLD)
            for skill, s, hits, _ in rows[:top]:
                print("  {:<26} {:>3}  {}".format(skill, s, "、".join(hits)))
        print("结论：无匹配技能，按通用能力处理。")
        return 0

    for idx, (skill, s, hits, note) in enumerate(rows[:top], 1):
        print("{}. {:<26} 得分 {:>3}".format(idx, skill, s))
        print("   命中：{}".format("、".join(hits[:6])))
        if idx == 1 and note:
            print("   注意：{}".format(note[:80]))
    print("-" * 60)
    print("建议加载：", rows[0][0])
    if len(rows) > 1 and rows[1][1] >= rows[0][1] * 0.7:
        print("提示：与 {} 得分接近，若任务跨两类可考虑串联执行。".format(rows[1][0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
