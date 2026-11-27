import base64
import html
import json
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta
import feedparser
import requests
from google import genai
from google.genai import types

# ================= [1. 기존 키 3개 (환경변수에서 자동 로드)] =================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ================= [2. GitHub 웹 업로드 정보] =================
GITHUB_USER = "no1frieza-cyber"
GITHUB_REPO = "stock-report"
GITHUB_TOKEN = os.environ.get("MY_GITHUB_TOKEN", "")
# =====================================================================

SAVE_DIR = "/tmp/stock" if os.environ.get("GITHUB_ACTIONS") == "true" else r"C:\stock"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

EXCLUDED_KEYWORDS = ("ETF", "ETN", "인버스", "곱버스")
ETF_BRANDS = (
    "KODEX", "TIGER", "RISE", "ACE", "SOL", "HANARO", "PLUS",
    "KIWOOM", "KOSEF", "KBSTAR", "ARIRANG", "TIMEFOLIO", "WON",
    "1Q", "KOACT", "BNK", "UNICORN", "FOCUS", "TRUSTON", "VITA",
    "HK", "마이티", "히어로즈", "에셋플러스", "파워", "마이다스",
    "TREX", "KINDEX", "SMART",
)

US_EXCLUDED_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "TQQQ", "SQQQ", "SOXL", "SOXS",
    "TSLL", "TSLS", "NVDL", "NVDS", "MSTU", "MSTX", "CONL", "UVXY",
    "VXX", "TLT", "HYG", "GLD", "SLV", "IBIT", "FBTC", "ARKK",
    "XLF", "XLE", "XLK", "SMH", "BOIL", "KOLD", "LABU", "YANG",
    "YINN", "FXI", "EEM", "VOO", "VTI", "SCHD", "JEPI", "JEPQ",
}
US_EXCLUDED_KEYWORDS = (
    "ETF", "ETN", "PROSHARES", "DIREXION", "GRANITESHARES",
    "YIELDMAX", "DEFIANCE", "ISHARES", "SPDR", "VANGUARD",
    "INVESCO", "2X ", "3X ", "ULTRA ", "INVERSE ", "SHORT ",
    "LEVERAGED", "레버리지", "인버스",
)

SORT_SCRIPT = r"""
<script>
document.addEventListener("DOMContentLoaded", () => {
  const table = document.getElementById("stockTable");
  if (!table) return;
  const tbody = table.querySelector("tbody");
  const headers = table.querySelectorAll("th.sortable");

  function parseKrw(text) {
    const clean = text.replace(/,/g, "").replace(/\s+/g, "");
    if (!clean || clean === "-") return -1;
    let total = 0;
    const jo = clean.match(/(\d+(?:\.\d+)?)조/);
    const eok = clean.match(/(\d+(?:\.\d+)?)억/);
    const man = clean.match(/(\d+(?:\.\d+)?)만/);
    if (jo) total += parseFloat(jo[1]) * 1e12;
    if (eok) total += parseFloat(eok[1]) * 1e8;
    if (man) total += parseFloat(man[1]) * 1e4;
    if (total === 0) {
      const num = parseFloat(clean.replace(/[^0-9.-]/g, ""));
      return isNaN(num) ? -1 : num;
    }
    return total;
  }

  function parseUsd(text) {
    const clean = text.replace(/[,$\s]/g, "").toUpperCase();
    if (!clean || clean === "-") return -1;
    const m = clean.match(/^([0-9.]+)([BMK])?$/);
    if (m) {
      let val = parseFloat(m[1]);
      if (m[2] === "B") val *= 1e9;
      else if (m[2] === "M") val *= 1e6;
      else if (m[2] === "K") val *= 1e3;
      return val;
    }
    const num = parseFloat(clean.replace(/[^0-9.-]/g, ""));
    return isNaN(num) ? -1 : num;
  }

  function parseValue(cellText, type) {
    const text = cellText.trim();
    if (type === "number") {
      const num = parseFloat(text.replace(/[^0-9.-]/g, ""));
      return isNaN(num) ? -Infinity : num;
    }
    if (type === "krw") return parseKrw(text);
    if (type === "usd") return parseUsd(text);
    return text;
  }

  headers.forEach((th) => {
    th.addEventListener("click", () => {
      const colIndex = Array.from(th.parentNode.children).indexOf(th);
      const type = th.dataset.type || "text";
      const currentDir = th.dataset.dir;
      const defaultDir = (type === "number" || type === "krw" || type === "usd") && colIndex !== 0 ? "desc" : "asc";
      const nextDir = currentDir ? (currentDir === "asc" ? "desc" : "asc") : defaultDir;

      headers.forEach((h) => {
        h.removeAttribute("data-dir");
        h.classList.remove("asc", "desc");
      });
      th.dataset.dir = nextDir;
      th.classList.add(nextDir);

      const rows = Array.from(tbody.querySelectorAll("tr"));
      rows.sort((a, b) => {
        const valA = parseValue(a.children[colIndex].textContent, type);
        const valB = parseValue(b.children[colIndex].textContent, type);
        const cmp = type === "text" ? valA.localeCompare(valB, "ko") : valA - valB;
        return nextDir === "asc" ? cmp : -cmp;
      });

      rows.forEach((row) => tbody.appendChild(row));
    });
  });
});
</script>
"""

INDEX_SCRIPT = r"""
<script>
document.addEventListener("DOMContentLoaded", () => {
  const tabBtns = document.querySelectorAll(".market-tab");
  const panels = document.querySelectorAll(".market-panel");

  function activateTab(tabName) {
    tabBtns.forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tabName));
    panels.forEach((panel) => {
      panel.style.display = panel.id === "panel-" + tabName ? "block" : "none";
    });
  }

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      activateTab(target);
      history.replaceState(null, "", target === "us" ? "#us" : "#kr");
    });
  });

  if (window.location.hash === "#us") {
    activateTab("us");
  } else {
    activateTab("kr");
  }

  const krPanel = document.getElementById("panel-kr");
  if (krPanel) {
    const buttons = krPanel.querySelectorAll(".filter-btn");
    const groups = krPanel.querySelectorAll(".date-group");

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const filter = btn.dataset.filter;
        buttons.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");

        groups.forEach((group) => {
          const cards = group.querySelectorAll(".report-card");
          let visibleCount = 0;
          cards.forEach((card) => {
            const show = filter === "ALL" || card.dataset.mode === filter;
            card.style.display = show ? "flex" : "none";
            if (show) visibleCount++;
          });
          group.style.display = visibleCount > 0 ? "block" : "none";
        });
      });
    });
  }

  // ===== 수동 갱신 버튼 (GitHub Actions workflow_dispatch) =====
  const TOKEN_KEY = "stock_report_gh_token";
  const controls = document.getElementById("refreshControls");
  const modeSelect = document.getElementById("modeSelect");
  const refreshBtn = document.getElementById("refreshBtn");
  const tokenBtn = document.getElementById("tokenBtn");
  const statusEl = document.getElementById("refreshStatus");

  function askToken() {
    const current = localStorage.getItem(TOKEN_KEY) || "";
    const input = prompt(
      "GitHub 토큰(github_pat_...)을 입력하세요.\n(이 기기 브라우저에만 저장되며 외부로 노출되지 않습니다. 삭제하려면 빈 칸으로 확인)",
      current
    );
    if (input === null) return null;
    const trimmed = input.trim();
    if (trimmed) {
      localStorage.setItem(TOKEN_KEY, trimmed);
      statusEl.textContent = "🔑 토큰이 이 브라우저에 저장되었습니다.";
      return trimmed;
    } else {
      localStorage.removeItem(TOKEN_KEY);
      statusEl.textContent = "🗑️ 저장된 토큰이 삭제되었습니다.";
      return "";
    }
  }

  if (tokenBtn) {
    tokenBtn.addEventListener("click", askToken);
  }

  if (refreshBtn && controls) {
    refreshBtn.addEventListener("click", async () => {
      let token = localStorage.getItem(TOKEN_KEY);
      if (!token) {
        token = askToken();
        if (!token) return;
      }

      const repo = controls.dataset.repo;
      const prevUpdated = controls.dataset.updated;
      const mode = modeSelect ? modeSelect.value : "auto";

      refreshBtn.disabled = true;
      statusEl.textContent = `⏳ [${mode.toUpperCase()}] 리포트 갱신을 요청하는 중...`;

      try {
        const res = await fetch(
          `https://api.github.com/repos/${repo}/actions/workflows/stock.yml/dispatches`,
          {
            method: "POST",
            headers: {
              "Authorization": `Bearer ${token}`,
              "Accept": "application/vnd.github+json",
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ ref: "main", inputs: { mode: mode } }),
          }
        );

        if (res.status === 401 || res.status === 403) {
          throw new Error("토큰 인증 실패 — 🔑 버튼을 눌러 토큰과 Actions(Read and write) 권한을 확인하세요.");
        }
        if (!res.ok) {
          throw new Error(`요청 실패 (HTTP ${res.status})`);
        }

        statusEl.textContent = "✅ 갱신 시작됨! 리포트 생성 및 배포
