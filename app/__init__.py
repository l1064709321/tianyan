"""novel-agent 应用包。

版本号单一来源: 后端 FastAPI 元信息与前端静态资源缓存破坏后缀均引用此常量,
避免在前端 HTML 里手动维护 ?v=N 这种与后端脱节的递增数字。
升级版本时只需改这一处。

版本号与 git 发布 tag 保持一致 (见 `git tag` / `git describe --tags`)。
"""
__version__ = "0.0.0.0.0.0.3"
