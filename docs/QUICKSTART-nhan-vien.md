# Hướng dẫn nhanh (nhân viên) — [LEGACY / ENGINEERING REFERENCE]

> ⚠️ **LƯU Ý QUAN TRỌNG CHO NHÂN VIÊN (STAFF V1):**
> Tài liệu này mô tả **quy trình CLI cũ (v2.3.4 legacy)** dùng cho mục đích kỹ thuật / kiểm thử nội bộ. 
> Nhân viên vận hành **KHÔNG SỬ DỤNG** quy trình này để bắt đầu.
> 👉 **Quy trình chuẩn hiện tại (v2.4.0+):**
> 1. Nhấp đúp vào **`Start-AMZ-Toolkit.bat`** (khởi động console tại `http://127.0.0.1:8780`).
> 2. Đọc hướng dẫn vận hành chuẩn tại: **[`docs/QUICKSTART-DASHBOARD-V1.md`](QUICKSTART-DASHBOARD-V1.md)**.
>
> *(Nội dung bên dưới được lưu lại chỉ nhằm mục đích tham chiếu kỹ thuật và tương thích ngược).*

---

> Chỉ **sếp (owner)** được chạy các lệnh `--approve-*`. Nhân viên chuẩn bị file; sếp duyệt.
> Trên Windows dùng `python`; trên Mac/Linux có thể là `python3`. Ví dụ dưới dùng `python`.

## Chuẩn bị dự án
1. **Tạo dự án:** `python pipeline.py runs/<ngách> --init-project --decoration-method "machine embroidery"`
2. **Tạo file cổng trống:** `python pipeline.py runs/<ngách> --scaffold-gate-files`
   → sinh ra 6 file JSON (RELEVANCE-REVIEW, PRODUCT-FEASIBILITY, FBM-FEASIBILITY, CATALOG-STRUCTURE,
   PERSONALIZATION-RULES, CLAIMS-EVIDENCE). Mở từng file, điền `"decision"` (GO / APPROVED / VERIFIED)
   kèm `reviewer` và `reviewed_at`. **Không** có quyết định = cổng vẫn INCOMPLETE (an toàn).
3. **Điền dữ liệu:** `demand-input.csv` (5 nguồn), `economics-input.csv` (giá + mọi chi phí), `listing.json`.

## Chạy pipeline
4. `python pipeline.py runs/<ngách> "Tên dự án"`
   - Công cụ đọc các file cổng và tự đặt trạng thái từng cổng (không sửa tay PROJECT-MANIFEST.json).
   - Đọc dòng **PUBLICATION LOCKED** ở cuối và mục **NEXT** — nó nói việc kế tiếp cần làm.
5. **Xem việc kế tiếp bất cứ lúc nào:** `python pipeline.py runs/<ngách> --next`

## Ảnh thật + bằng chứng thêu (khem docs/REAL-PHOTO-SOP.md)
6. Chụp ảnh main + macro thật, để vào thư mục dự án, điền `creative-brief.json` + review có `asset_hash`.
7. `python creative/creative_edge.py runs/<ngách>` → sinh EMBROIDERY-PROOF / VISUAL-CONSISTENCY / ...

## Ý nghĩa trạng thái
- **GO / APPROVED / VERIFIED / CONSISTENT / PROVEN** = đạt.
- **INCOMPLETE** = thiếu dữ liệu, bổ sung rồi chạy lại.
- **REVISE / REVIEW** = cần người kiểm (ví dụ IP có từ lạ) — báo sếp.
- **BLOCKED** = cấm (IP vi phạm, claim sai, lỗ vốn) — **dừng, báo sếp ngay**.

## Sếp duyệt (chỉ owner, đúng thứ tự)
8. `python pipeline.py runs/<ngách> --approve-main-image --asset main.png --by owner`
9. `python pipeline.py runs/<ngách> --approve-creative --by owner`
10. `python pipeline.py runs/<ngách> --approve-final --by owner`
   - Mỗi lần duyệt được ràng buộc bằng hash. Sửa bất kỳ file đã duyệt → tự động **hủy duyệt** và khóa lại.
   - `--approve-final` **từ chối** nếu chưa duyệt creative.

## Lưu ý
11. **File nào nhập tay vào Seller Central?** Gói publish — **sếp làm tay**. Công cụ KHÔNG bao giờ nối vào Amazon.
12. **Lỗi?** Đọc Status / Reason / Next action. Lỗi dữ liệu thường không có traceback.
