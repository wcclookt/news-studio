from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    desc: str = ""
    published_at: str = ""
    score: float = 0.0
    category: str = "other"
    summary: str = ""
    reason: str = ""
    title_zh: str = ""
    image: str = ""
    credibility: str = ""      # 可信度: 可信 / 较可信 / 中等 / 存疑 / 不可信
    is_valuable: bool = True   # 是否有价值(过滤无意义口水仗等)
    comment: str = ""          # AI 解读/评价
    utility: float = 5.0       # 对普通人的实用性 0-10

    def display_title(self):
        if self.title_zh and self.title_zh != self.title:
            return f"{self.title}\n译文：{self.title_zh}"
        return self.title_zh or self.title

    def to_dict(self):
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "desc": self.desc,
            "published_at": self.published_at,
            "image": self.image,
            "credibility": self.credibility,
            "comment": self.comment,
        }


def now_str():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
