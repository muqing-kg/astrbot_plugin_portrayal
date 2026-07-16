from dataclasses import asdict, dataclass, fields
from typing import Any


_PLATFORM_ALIASES = {
    "qq": "qq",
    "aiocqhttp": "qq",
    "onebot": "qq",
    "wechat": "wechat",
    "weixin": "wechat",
    "wx": "wechat",
    "wecom": "wechat",
    "wework": "wechat",
    "gewechat": "wechat",
}


def normalize_platform(platform: str | None) -> str:
    """把各种平台名归一成 qq / wechat / generic。"""
    key = str(platform or "").strip().lower()
    if not key:
        return "qq"
    return _PLATFORM_ALIASES.get(key, "generic")


def normalize_sex(value: Any) -> str:
    """统一性别展示，兼容 QQ / 微信常见返回值。"""
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if lower in {"male", "m", "1", "man"} or text in {"男", "男性"}:
        return "男"
    if lower in {"female", "f", "2", "woman"} or text in {"女", "女性"}:
        return "女"
    if lower in {"unknown", "0", "none", "null"} or text in {"未知", "保密"}:
        return ""
    return text


@dataclass(slots=True)
class UserProfile:
    user_id: str
    nickname: str = ""
    remark: str = ""

    sex: str = ""
    birthday: str = ""

    phoneNum: str = ""
    eMail: str = ""

    address: str = ""

    long_nick: str = ""

    portrait: str = ""
    timestamp: int = 0
    # qq / wechat / generic
    platform: str = "qq"

    # 最近一次生成画像时的附属信息（兼容旧 JSON 无字段）
    last_command: str = ""
    last_message_count: int = 0
    last_query_rounds: int = 0
    last_stats: dict | None = None

    def __post_init__(self) -> None:
        self.platform = normalize_platform(self.platform)
        self.sex = normalize_sex(self.sex)
        if self.last_stats is None:
            self.last_stats = {}
        elif not isinstance(self.last_stats, dict):
            self.last_stats = {}

    @property
    def id_label(self) -> str:
        if self.platform == "wechat":
            return "微信ID"
        if self.platform == "qq":
            return "QQ号"
        return "账号ID"

    @property
    def id_chip(self) -> str:
        if not self.user_id:
            return ""
        if self.platform == "wechat":
            return f"微信 {self.user_id}"
        if self.platform == "qq":
            return f"QQ {self.user_id}"
        return f"ID {self.user_id}"

    @property
    def signature_label(self) -> str:
        return "个性签名" if self.platform == "wechat" else "签名"

    def to_dict(self) -> dict:
        data = asdict(self)
        # 空统计不写 null，统一写成 {}
        if not data.get("last_stats"):
            data["last_stats"] = {}
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        allowed = {f.name for f in fields(cls)}
        payload = {k: v for k, v in data.items() if k in allowed}
        if "sex" not in payload and "gender" in data:
            payload["sex"] = data.get("gender", "")
        stats = payload.get("last_stats")
        if stats is None:
            payload["last_stats"] = {}
        elif not isinstance(stats, dict):
            payload["last_stats"] = {}
        return cls(**payload)

    def attach_generation_meta(
        self,
        *,
        command: str = "",
        message_count: int = 0,
        query_rounds: int = 0,
        stats: Any = None,
    ) -> None:
        """写入最近一次生成画像的元数据与本地统计快照。"""
        self.last_command = str(command or "") or self.last_command
        if message_count:
            self.last_message_count = int(message_count)
        if query_rounds:
            self.last_query_rounds = int(query_rounds)
        if stats is None:
            return
        if hasattr(stats, "to_dict"):
            self.last_stats = dict(stats.to_dict())
        elif isinstance(stats, dict):
            self.last_stats = dict(stats)

    @classmethod
    def from_qq_data(
        cls,
        user_id: str,
        *,
        data: dict[str, Any],
    ) -> "UserProfile":
        return cls(
            user_id=str(user_id),
            nickname=data.get("nickname", "") or data.get("card", "") or "",
            remark=data.get("remark", ""),
            sex=data.get("sex", "") or data.get("gender", ""),
            birthday=data.get("birthday", ""),
            phoneNum=data.get("phone", "") or data.get("phoneNum", ""),
            eMail=data.get("email", "") or data.get("eMail", ""),
            address=data.get("address", "") or data.get("area", ""),
            long_nick=data.get("long_nick", "") or data.get("signature", ""),
            platform="qq",
        )

    @classmethod
    def from_wechat_data(
        cls,
        user_id: str,
        *,
        data: dict[str, Any],
    ) -> "UserProfile":
        """从微信侧资料字段构建画像。

        兼容常见别名：wxid / username / nickname / remark / signature / region 等。
        """
        nickname = (
            data.get("nickname")
            or data.get("nick_name")
            or data.get("display_name")
            or data.get("name")
            or ""
        )
        remark = data.get("remark") or data.get("remark_name") or data.get("alias") or ""
        sex = data.get("sex") or data.get("gender") or data.get("sex_type") or ""
        birthday = data.get("birthday") or data.get("birth") or ""
        # 微信常把省市拆开，优先拼完整地区
        region_parts = [
            str(data.get("country") or "").strip(),
            str(data.get("province") or "").strip(),
            str(data.get("city") or "").strip(),
        ]
        joined_region = " ".join(p for p in region_parts if p)
        address = (
            data.get("address")
            or data.get("region")
            or joined_region
            or data.get("city")
            or data.get("province")
            or data.get("country")
            or ""
        )

        signature = (
            data.get("signature")
            or data.get("long_nick")
            or data.get("sign")
            or data.get("bio")
            or ""
        )
        wx_id = (
            str(user_id)
            or str(data.get("wxid") or "")
            or str(data.get("username") or "")
            or str(data.get("user_id") or "")
        )

        return cls(
            user_id=wx_id,
            nickname=str(nickname),
            remark=str(remark),
            sex=sex,
            birthday=str(birthday),
            phoneNum=str(data.get("phone") or data.get("mobile") or ""),
            eMail=str(data.get("email") or ""),
            address=str(address),
            long_nick=str(signature),
            platform="wechat",
        )

    @classmethod
    def from_platform_data(
        cls,
        user_id: str,
        *,
        data: dict[str, Any],
        platform: str | None = None,
    ) -> "UserProfile":
        """按平台选择 QQ / 微信字段映射。"""
        kind = normalize_platform(platform or data.get("platform"))
        if kind == "wechat":
            return cls.from_wechat_data(user_id, data=data)
        if kind == "qq":
            return cls.from_qq_data(user_id, data=data)
        profile = cls.from_qq_data(user_id, data=data)
        profile.platform = "generic"
        if not profile.long_nick:
            profile.long_nick = str(
                data.get("signature") or data.get("bio") or data.get("sign") or ""
            )
        return profile

    def to_text(self) -> str:
        meta = (
            ("user_id", self.id_label),
            ("nickname", "昵称"),
            ("remark", "备注"),
            ("sex", "性别"),
            ("birthday", "生日"),
            ("phoneNum", "电话"),
            ("eMail", "邮箱"),
            ("address", "地区" if self.platform == "wechat" else "现居"),
            ("long_nick", self.signature_label),
        )

        lines = [
            f"{label}：{value}"
            for key, label in meta
            if (value := getattr(self, key)) not in ("", None, 0)
        ]

        return "\n".join(lines)

    def to_chips(self, *, max_items: int = 6) -> list[str]:
        """生成卡片顶部资料 chips。

        微信场景不展示 chips（账号/性别/签名等对微信侧无用或不宜露出）。
        QQ 场景保留账号与基础资料标签。
        """
        if self.platform == "wechat":
            return []

        candidates = [
            self.id_chip,
            self.sex,
            self.birthday,
            self.remark,
            self.long_nick,
            self.address,
        ]
        chips: list[str] = []
        for value in candidates:
            text = str(value or "").strip()
            if not text:
                continue
            if text in chips:
                continue
            chips.append(text)
            if len(chips) >= max_items:
                break
        return chips
