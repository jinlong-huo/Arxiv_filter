# Tools

组内工具清单。所有工具按"装好就能用"的标准写清楚。

---

## 1. arXiv Daily Digest

每天自动从 arXiv 抓取论文，按关键词打分，发送 top-N 到邮箱。

**安装**

```bash
# 依赖
pip install feedparser

# 配置邮箱密码（二选一）
export ARXIV_DIGEST_EMAIL_PASSWORD="你的Gmail应用专用密码"
# 或者: echo "密码" > .email_password

# 测试
python3 arxiv_digest/Arxiv_filter.py --send
```

**设为每天自动跑**

- macOS: `./setup.zsh`
- Windows (管理员 PowerShell): `.\setup.ps1`

详细说明见 [README.md](../README.md)。

---

## 2. Git & GitHub

所有文档和代码都在 GitHub 上。基本流程：

```bash
git checkout -b your-name/feature
# 修改文件...
git add .
git commit -m "描述你做了什么"
git push -u origin your-name/feature
# 在 GitHub 上创建 PR
```

---

## 3. Zotero（推荐）

论文管理工具。建议和 paper-notes 配合使用：
- Zotero 管 PDF 和引用
- paper-notes/ 管你的理解和批注

---

## 4. Python 环境

推荐 conda 或 venv：

```bash
conda create -n research python=3.12
conda activate research
pip install feedparser numpy matplotlib
```

---

> 有新工具就加进来。每个工具写清楚：装什么、怎么用、碰到问题找谁。
