const pageMap = {
    dashboard: {
      title: "工作台",
      desc: "统一查看系统状态、问答概览和设备页面。"
    },
    chat: {
      title: "智能问答",
      desc: "面向不同角色的核心问答交互页面。"
    },
    users: {
      title: "用户管理",
      desc: "统一展示平台中的用户、角色与权限信息。"
    },
    records: {
      title: "问答记录",
      desc: "查看提问记录与问答过程页面。"
    },
    analysis: {
      title: "教学分析",
      desc: "展示高频问题、知识点分布和页面分析内容。"
    },
    robot: {
      title: "机器人控制",
      desc: "展示机器人状态、地图和控制面板。"
    }
  };
  
  let selectedRole = "admin";
  
  function setRoleText(role) {
    if (role === "admin") return "管理员";
    if (role === "teacher") return "教师";
    return "学生";
  }
  
  function login() {
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value.trim();
    const loginError = document.getElementById("loginError");
  
    if (!username || !password) {
      loginError.textContent = "请输入账号和密码。";
      return;
    }
  
    loginError.textContent = "";
  
    document.getElementById("loginPage").classList.add("hidden");
    document.getElementById("appPage").classList.remove("hidden");
    document.getElementById("currentRole").textContent = setRoleText(selectedRole);
  
    resetChatData();
  }
  
  function logout() {
    document.getElementById("appPage").classList.add("hidden");
    document.getElementById("loginPage").classList.remove("hidden");
  
    document.getElementById("username").value = "";
    document.getElementById("password").value = "";
    document.getElementById("loginError").textContent = "";
  
    resetChatData();
  }
  
  function showPage(pageId, btn) {
    document.querySelectorAll(".page").forEach((page) => {
      page.classList.remove("active");
    });
  
    const targetPage = document.getElementById(pageId);
    if (targetPage) {
      targetPage.classList.add("active");
    }
  
    document.querySelectorAll(".menu button").forEach((button) => {
      button.classList.remove("active");
    });
  
    if (btn) {
      btn.classList.add("active");
    }
  
    document.getElementById("pageTitle").textContent = pageMap[pageId].title;
    document.getElementById("pageDesc").textContent = pageMap[pageId].desc;
  }
  
  function openFromDashboard(pageId) {
    const targetBtn = [...document.querySelectorAll(".menu button")]
      .find((btn) => btn.textContent.includes("智能问答"));
    showPage(pageId, targetBtn);
  }
  
  function sendMessage() {
    const input = document.getElementById("chatInput");
    const chatBody = document.getElementById("chatBody");
    const sessionList = document.getElementById("sessionList");
  
    if (!input || !chatBody || !sessionList) return;
  
    const text = input.value.trim();
    if (!text) return;
  
    const userMsg = document.createElement("div");
    userMsg.className = "message user";
    userMsg.textContent = text;
    chatBody.appendChild(userMsg);
  
    const botMsg = document.createElement("div");
    botMsg.className = "message bot";
    botMsg.textContent = "这是纯前端静态演示版本。当前回复由页面本地脚本生成，用于展示问答交互效果。";
    chatBody.appendChild(botMsg);
  
    const newSession = document.createElement("div");
    newSession.className = "session-item";
    newSession.textContent = text.length > 16 ? text.slice(0, 16) + "..." : text;
    sessionList.appendChild(newSession);
  
    chatBody.scrollTop = chatBody.scrollHeight;
    input.value = "";
  }
  
  function clearChat() {
    const chatBody = document.getElementById("chatBody");
    if (!chatBody) return;
  
    chatBody.innerHTML = `
      <div class="message bot">
        当前聊天内容已清空。
      </div>
    `;
  }
  
  function clearSessions() {
    const sessionList = document.getElementById("sessionList");
    if (!sessionList) return;
  
    sessionList.innerHTML = `
      <div class="session-item active">暂无历史会话</div>
    `;
  }
  
  function resetChatData() {
    const chatBody = document.getElementById("chatBody");
    const sessionList = document.getElementById("sessionList");
    const chatInput = document.getElementById("chatInput");
  
    if (chatBody) {
      chatBody.innerHTML = `
        <div class="message bot">
          欢迎使用实验助手平台。这里是纯前端演示页面，你可以输入问题查看页面交互效果。
        </div>
      `;
    }
  
    if (sessionList) {
      sessionList.innerHTML = `
        <div class="session-item active">默认会话</div>
      `;
    }
  
    if (chatInput) {
      chatInput.value = "";
    }
  }
  
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".role-btn").forEach((btn) => {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".role-btn").forEach((item) => item.classList.remove("active"));
        this.classList.add("active");
        selectedRole = this.dataset.role;
      });
    });
  
    const chatInput = document.getElementById("chatInput");
    if (chatInput) {
      chatInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          sendMessage();
        }
      });
    }
  });