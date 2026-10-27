# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""文本切词、路径匹配与相似度计算。

这一层刻意只用 Python 标准库，不依赖任何第三方包，保证评测机上 `python3 scripts/xxx.py` 就能跑。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set

_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")
_PATH_TOKEN = re.compile(r"[\w./\\-]+\.(?:py|cpp|h|hpp|cc|c|md|json|yml|yaml|test|sql|l|y|txt|cmake|sh)")

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "be", "with",
    "我", "的", "了", "吗", "呢", "把", "给", "让", "要", "在", "和", "与", "或", "个",
}


def tokenize(text: str) -> List[str]:
    """中英混排切词。

    英文按单词切；中文按「单字 + 相邻二字组合」切。
    中文不用词典分词，是因为词典分词需要额外依赖，而且在「B+树 页分裂」这种
    术语混排的场景下，按字和二字组切反而更稳，不会因为分词器没收录而丢词。
    """
    if not text:
        return []
    lowered = text.lower()
    tokens: List[str] = [m.group(0) for m in _ASCII_TOKEN.finditer(lowered) if m.group(0) not in _STOPWORDS]
    cjk = _CJK_CHAR.findall(text)
    tokens.extend(cjk)
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i] + cjk[i + 1])
    return tokens


def token_set(text: str) -> Set[str]:
    return set(tokenize(text))


def overlap_ratio(query: Set[str], doc: Set[str]) -> float:
    """任务词被文档覆盖的比例：|query ∩ doc| / |query|。取 0~1。"""
    if not query or not doc:
        return 0.0
    return len(query & doc) / float(len(query))


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def combine_scores(*parts: Sequence[float]) -> float:
    """加权求和，权重由调用方通过 parts 的顺序约定（这里只是求平均）。"""
    if not parts:
        return 0.0
    return sum(parts) / float(len(parts))


def glob_to_regex(pattern: str) -> re.Pattern:
    """把工程里常见的路径通配写法编译成正则。

    - ``**`` 跨目录
    - ``*`` 不跨目录
    - ``?`` 单个字符

    用 fnmatch 会把 ``*`` 当成能跨目录，那是错的（``src/sql/*`` 会命中
    ``src/sql/parser/x.cpp``），所以这里自己实现。
    """
    out = ["^"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


_GLOB_CACHE: Dict[str, re.Pattern] = {}


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    """判断相对路径是否命中任一通配模式。"""
    norm = path.replace("\\", "/")
    for pattern in patterns:
        if not pattern:
            continue
        norm_pattern = pattern.replace("\\", "/")
        regex = _GLOB_CACHE.get(norm_pattern)
        if regex is None:
            regex = glob_to_regex(norm_pattern)
            _GLOB_CACHE[norm_pattern] = regex
        if regex.match(norm):
            return True
    return False


def extract_path_mentions(text: str) -> List[str]:
    """从自然语言里抠出像文件路径的片段，例如「改一下 bplus_tree.cpp 的分裂逻辑」。"""
    if not text:
        return []
    return [m.group(0) for m in _PATH_TOKEN.finditer(text)]
