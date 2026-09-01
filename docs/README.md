# 台股盤後行動選股 PWA

## 策略與成交量篩選

- 可依今日成交量張數區間、近 N 日平均量、今日相對均量倍數篩選。
- 可命名保存多套策略，之後載入、覆蓋或刪除。
- 策略同時保存在目前裝置的 localStorage 與 IndexedDB，重新開啟後會自動還原。
- 產業別可複選，股票清單可設定第一、第二、第三排序順位。
- 個股頁可按「返回」、右上角關閉、瀏覽器返回，或從畫面左側向右滑關閉。
- 基本面頁提供 MoM／YoY 圖例、百分比座標與近 12 個月明細表。
- EPS 會標示「單季」或「本年度累計」；不會把累計值誤當成單季值篩選。
- 財務健診分開列出通用「財務亮點」與「財務風險」，並顯示近四季 EPS、
  營收成長、毛利率、營益率、ROE、負債比、流動比率、現金含量與估值。
- 可展開查看近 12 季營收、EPS、利潤率、負債比及營業現金流；資料不足時
  只顯示缺漏，不會硬判斷為好或壞。
- Android 與 iPhone 的個人策略仍不會自動互相同步；刪除 App 或網站資料前應另行備份。

這個資料夾可直接部署為 GitHub Pages，iPhone 使用 Safari 開啟後，
可透過「分享 → 加入主畫面」安裝成獨立 Web App。

## 啟用 GitHub Pages

1. 將整個 `docs` 資料夾與 `.github/workflows/pages.yml` 放進儲存庫的 `main` 分支。
2. 進入 GitHub 儲存庫的 `Settings → Pages`。
3. `Source` 選擇 `GitHub Actions`。
4. 到 `Actions → 部署 iPhone PWA` 執行一次 `Run workflow`；之後盤後資料更新成功會自動部署。
5. 等待部署完成後，開啟：
   `https://888god888.github.io/tw-ray-stock-cloud-data/`

## 資料更新

`部署 iPhone PWA` Workflow 會從固定 Release `latest` 取得：

- `manifest.json`：版本、日期、SHA-256。
- `snapshot.json.gz`：完整市場盤後快照。

資料會與 PWA 一起發布到同一個 GitHub Pages 網域，避免 Safari 的 CORS 限制。
股票快照會解壓後存進 IndexedDB；離線時仍可使用最近一次成功下載的資料。
App 程式外殼由 Service Worker 快取，股票快照不會放進 Service Worker Cache。

## 本機預覽

在儲存庫根目錄執行：

```bash
python -m http.server 8000 --directory docs
```

再開啟 `http://localhost:8000`。Service Worker 在 `localhost` 可正常註冊。
