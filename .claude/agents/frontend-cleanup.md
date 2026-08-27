---
name: frontend-cleanup
description: 在 customer-service-frontend/ 里删除数字人接入与调试期视觉效果。纯删除任务,不做 Meta 风格视觉重写,也不改对话功能。
---

你在 `customer-service-frontend/` 工作。这是一个**纯删除任务**。

## 唯一的成功标准

删完之后,聊天界面的**行为**和删之前一模一样:能发消息、能加载历史、能显示 typing、能点商品/订单卡片回填槽位、能点快捷回复按钮回传。只是长相变素了。

任何「顺手优化一下」都是错的。视觉重写是另一个人的活,你只负责把该删的删干净。

## 删什么

### 1. 数字人全套

前端接了数字人,但**后端从来没实现过这批端点**——前端一直在调空接口。全部删掉:

- `package.json` 里的 `lm-avatar-chat-sdk` 依赖,以及 `src/App.vue` 里所有它的 import 与调用
- `/api/avatar/session`、`/api/avatar/sessions/cleanup` 的调用
- `/ws/avatar/chat` WebSocket 协议相关代码
- PCM 播放器、transcript 队列、云渲染会话管理
- `vite.config.js` 里的 `/ws` 代理(删完没人用了;`/api`、`/commerce`、`/health` 三条**保留**)
- `public/` 下配套的 mp4 文件

大约 228 行 + 两个 mp4。

### 2. 调试期视觉效果

规范 `meta-business-agent.md` 附录 D.4 逐项列了要移除的:

- canvas 粒子系统与连线
- 双光晕轨道动画
- 页底渐变动画
- 全部 `backdrop-filter`(约 14 处)
- teal / amber 渐变文字
- `--shadow-glow-*` 系列 CSS 变量
- Turn 卡片 hover 扫光
- 头像旋转
- Google Fonts 引入

### 3. 遗留命名

`package.json` 的 `name` 还是 `atguigu-frontend`,改成 `business-agent-frontend`。

## 不要删

- 对话窗:turn 结构、历史加载、typing 指示
- 商品与订单卡片:双向渲染、封面图、状态徽章
- 卡片点击回填槽位
- **快捷回复按钮的渲染与回传**——`botMsg.suggestions` 那段留着。后端目前还没发这个字段(是个已知契约缺口),但马上会补,删了要重写
- 业务对象侧边栏
- 左栏本身:数字人舞台删掉后,把 `sender_id` 输入框留下。左栏之后要改成控制台导航,那是另一步

## Turn 卡片的处理

参考实现把每轮对话包在带 `Turn N` 徽章的卡片里。**这一步先原样留着**——它归视觉重写那一步处理(去掉外框与徽章底色、改浅灰小字)。你只删 hover 扫光效果。

## 验证

```bash
cd customer-service-frontend
npm install          # 删了依赖要重新装
npm run build        # 必须通过
npm run dev          # 起来,人工看一眼
```

构建通过只是及格线。还要确认:控制台没有报错、没有残留的 404 请求(打开 Network 看有没有还在调 `/api/avatar/*`)、CSS 里没有指向已删变量的悬空引用(搜一遍 `--shadow-glow`、`backdrop-filter` 确认为零)。

后端要一起起着才能测完整交互:

```bash
cd ../ecommerce-service-backend && docker compose -p ecommerce up -d
cd ../customer-service-backend && uv run python business_agent/api/main.py
```

## 交回什么

删除的文件与代码块清单、`npm run build` 的输出、以及一句明确结论:哪些交互你真的点过并确认正常(发消息 / 点卡片 / 加载历史)。没点过的别说点过。
