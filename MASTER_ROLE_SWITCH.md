# 可還原的管理者身份切換

此功能預設關閉。沿用家屬／長者／看護三種身份，不新增可任意取得的 master 身份。
只有 Render 環境變數明確允許的 LINE 帳號可使用，且只能在 Bot 私人聊天室切換。

## 部署與啟用

1. 將本次修改的 `app.py` 和新增的 `master_roles.py` 一起部署到目前的 Render Web Service。
2. 確認 `DATABASE_URL` 指向 LINE Bot 主資料庫（包含 `app_users`、`patients`）；不要指向只有 RAG 藥品知識的另一個資料庫。
3. 設定環境變數：

   ```text
   MASTER_ROLE_SWITCH_ENABLED=true
   MASTER_LINE_USER_IDS=<你的 U 開頭 LINE User ID>
   ```

   多個 ID 以逗號分隔；請只加入需要測試的管理者。不要將真實 ID 提交到公開 GitHub。
4. 重新部署。啟動時自動建立 `master_role_sessions`，不改動其他人的身份或家庭指派。
5. 在 Bot 私訊輸入「身份狀態」，再輸入「切換身份」。

## LINE 指令

| 指令 | 功能 |
| --- | --- |
| 我的 LINE ID | 私訊回覆目前使用者自己的 LINE User ID |
| 切換身份 | 顯示家屬／長者／看護按鈕 |
| 切換為家屬 | 回到家屬操作；原始還原備份仍保留 |
| 切換為長者 | 使用專用測試患者操作長者功能 |
| 切換為看護 | 自動提供同一位專用測試患者 |
| 身份狀態 | 查看身份及測試是否使用中 |
| 還原身份 | 還原切換前的身份、語言、選單及看護患者選擇 |
| 停用身份切換 | 先還原，再永久關閉此帳號的切換入口；舊按鈕也無效 |

## 驗證藥袋分析儲存

1. 「切換為長者」→ 我的藥物 → 拍攝藥袋 → 傳照片。
2. 等待辨識完成；既有程式將結果寫入 `ai_medication_scans`。
3. 在主資料庫執行以下唯讀查詢，確認這次的 `completed`、結果長度及患者關聯：

   ```sql
   SELECT s.created_at, s.processing_status,
          length(s.original_text) AS analysis_characters,
          s.parsed_result IS NOT NULL AS has_json_result,
          s.patient_id = m.test_patient_id AS belongs_to_test_patient
   FROM master_role_sessions m
   JOIN app_users u ON u.id = m.user_id
   JOIN ai_medication_scans s ON s.patient_id = m.test_patient_id
   WHERE u.line_user_id = '<你的 LINE User ID>'
   ORDER BY s.created_at DESC
   LIMIT 10;
   ```
4. 「切換為看護」傳照片，驗證同一個測試患者的分析紀錄。
5. 輸入「還原身份」，確認回到家屬及原始選單。
6. 測試完成輸入「停用身份切換」，再將 Render 的 `MASTER_ROLE_SWITCH_ENABLED` 改成 `false`。

## 還原範圍與資料保留

- 首次切換時的身份、語言、選單 ID、原有看護患者選擇持久備份在資料庫；連續切換不覆蓋備份。
- 家庭成員、真實患者綁定及看護指派不交換、不改寫。
- 測試患者不綁定任何真人 LINE 帳號；長者／看護透過測試分流取得它。
- 家屬身份仍是原本的家庭權限，操作家屬功能會操作原本家庭資料。
- 切換時清除尚未完成的表單及暫時選單，不恢復舊的未完成操作。
- 還原後將測試患者設為非啟用，保留掃描與其他測試紀錄；不自動刪除歷史資料。
- 測試長者／看護的 SOS、不舒服／異常通知停用，避免測試發出真實通知。
- LINE 選單更新與 PostgreSQL 不是同一筆交易。資料庫已切換但 LINE 更新失敗時，`menu_pending` 保留，下一次事件自動重試；可輸入「還原身份」。
- 移除允許 ID 或關閉環境變數後，已切換帳號會在下一次收到事件時自動還原；不是設定一改就立即還原。建議先在 LINE 還原，再關閉環境變數。

## 重新開放已停用的帳號

一般使用者不能透過 LINE 重新啟用。資料庫管理者可在確認帳號後執行：

```sql
UPDATE master_role_sessions s SET disabled = FALSE
FROM app_users u
WHERE s.user_id = u.id
  AND u.line_user_id = '<你的 LINE User ID>'
  AND s.active = FALSE
  AND s.menu_pending = FALSE;
```

仍須同時啟用 Render 環境變數及允許 ID。

## 完全移除程式功能

先執行「停用身份切換」，確認 `active=FALSE`、`menu_pending=FALSE`，再部署不含此功能的舊版本。
保留 `master_role_sessions` 和測試患者即可，不必刪表；它們可作為還原備份及掃描來源紀錄。

## 驗證範圍

本次提供 Python 語法檢查及離線狀態轉移測試，不會在測試中呼叫 LINE、OpenAI 或寫入正式資料庫。
仍須在實際 Render／PostgreSQL／LINE 環境完成上述驗證，尤其確認 `patients.linked_user_id` 允許 NULL，
`patients` 的其他必填欄位有預設值，以及 `master_role_sessions` 的建表權限。

```text
python -m unittest -v test_master_roles
python -m py_compile app.py master_roles.py
```
