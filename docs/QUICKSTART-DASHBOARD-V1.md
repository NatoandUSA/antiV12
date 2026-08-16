# Hướng dẫn nhanh (nhân viên) — Dashboard V1 / Workflow

> **Đây là quy trình hiện tại.** `docs/QUICKSTART-nhan-vien.md` (lệnh `pipeline.py`) là quy
> trình cũ, không còn dùng cho công việc mới — bỏ qua file đó.
> Chỉ **sếp (owner)** làm việc trong Seller Central. Nhân viên chuẩn bị file; công cụ này
> **không bao giờ** kết nối Amazon / Seller Central.

## 1. Mở bảng điều khiển

Bấm đúp file **`Start-AMZ-Toolkit.bat`**. Nó tự mở trình duyệt khi console sẵn sàng. Trang mở
ra ngay là **Workflow** — đây là màn hình chính, cho biết dự án đang ở bước nào và cần làm gì
tiếp theo.

## 2. Tạo / chọn dự án (workspace)

Trên trang Workflow, ngay dưới dòng **"Product workspace folder"**, có mục **"Product
workspaces"** — liệt kê mọi dự án đã có, kèm trạng thái (xem mục 6 bên dưới) và nút **Switch**
để đổi sang dự án đó ngay lập tức (không cần khởi động lại console).

**Tạo dự án mới:** gõ tên vào ô **"New product name"** (chỉ chữ, số, dấu gạch ngang — không
dấu, không khoảng trắng) rồi bấm **Create workspace**, xác nhận đúng cụm từ hiện ra. Console
tự tạo thư mục và chuyển sang dự án đó ngay — không cần tự tạo thư mục bằng tay, không cần
khởi động lại.

Dòng **"Product workspace folder"** ở đầu trang luôn cho biết chính xác thư mục dự án đang mở
— bấm **Copy path** để copy đường dẫn đó vào lệnh bên dưới.

## 3. Nhập Product Truth (Phase 6A) — **làm bước này trước** bước 8 (Keywords -> listing)

Đây là bước khai báo sự thật vật lý về sản phẩm (chất liệu, cách trang trí, v.v.) — bắt buộc
trước khi làm bước 8. Trang Workflow hiện hiển thị **"Product Truth (prerequisite)"** ở đầu
nhóm Build — không đánh số như 13 bước chính (đây là điều kiện tiên quyết, không phải bước 14),
nhưng có trạng thái và nút Copy command như các bước khác. Nếu bước 8 hiện dòng "Waiting on
Product Truth", quay lại đây trước.

```
python scripts/phase6a_build.py <đường-dẫn-thư-mục-dự-án>
```

Sau khi chạy xong, mở file **`<thư-mục-dự-án>/phase6/6A/PRODUCT-READINESS-REPORT.md`** — báo
cáo này liệt kê rõ: sự thật nào đã xác nhận, thông tin nào còn thiếu, việc nào sếp cần làm tiếp.
Đọc phần **"9. Owner information required"** nếu bị chặn.

## 4. Các bước còn lại — theo đúng thứ tự trên Workflow

Mỗi dòng trên Workflow có nút **Copy command** — copy đúng lệnh đó, dán vào terminal, chạy.
Không cần hiểu lệnh làm gì bên trong; chỉ cần trạng thái sau khi chạy xong đổi thành **READY**.

- **STALE** = có file mới hơn, cần chạy lại lệnh của bước đó.
- **BLOCKED** = bước trước chưa xong; xem "Waiting on stage …" để biết bước nào.
- **NOT_STARTED** = chưa có file, chạy lệnh copy được.
- **UNKNOWN** = có file nhưng đọc lỗi — báo sếp, đừng tự sửa file JSON bằng tay.
- **NOT_ACCEPTED** = code đã chạy đúng nhưng chưa qua review nội bộ — không phải lỗi thao tác.
- **OWNER_INPUT_REQUIRED** (chỉ ở dòng Product Truth) = đã chạy xong nhưng còn thiếu thông tin
  sếp cần xác nhận — mở `PRODUCT-READINESS-REPORT.md` (bước 3) để xem thiếu gì.

## 5. Chi phí / kinh tế (nền tảng Phase 7.1M, cho PPC — bước 11)

Không cần tự soạn file kinh tế từ đầu. Chạy lệnh sau một lần (đổi `<đường-dẫn-thư-mục-dự-án>`
theo dòng "Product workspace folder" ở bước 2):

```
python -m production.phase7_minimal_launch_foundation --run-dir <đường-dẫn-thư-mục-dự-án>
```

Công cụ tự sinh file mẫu tại **`<thư-mục-dự-án>/phase7/7.1M/PPC-ECONOMICS.template.json`**
(cùng file mẫu cho `LIVE-PRODUCT-STATE` và `PPC-PRODUCT-CONTRACT`). Mở file mẫu, điền số thật
(giá bán, phí Amazon, giá vốn, phí đóng gói, v.v.), lưu lại thành file mới (không ghi đè file
`.template.`).

Bước 11 (nút Copy command trên Workflow) sau đó cần thêm cờ trỏ vào các file đã điền —
`--live-state <file> --contract <file> --economics <file>`. Chạy `python -m
production.phase7_extended_launch_planning --help` để xem đúng tên từng cờ nếu không chắc.

Công cụ **không bao giờ tự đoán** chi phí còn thiếu — thiếu số nào, dừng lại chờ số đó, không
tự chạy PPC khi dữ liệu chưa đủ.

## 6. Dòng chữ "Workspace" đầu trang Workflow

- **TRUSTED** = dữ liệu đã đối chiếu khớp với gói đã duyệt.
- **UNVERIFIED** = dự án mới, chưa có gói nào để đối chiếu — bình thường.
- **HISTORICAL** = dự án cũ đã cách ly (ví dụ T2) — chỉ xem để tham khảo, **không dùng để ra
  quyết định mới**.

## 7. Không rõ phải làm gì?

Đọc đúng 3 chỗ theo thứ tự: dòng trạng thái (READY/BLOCKED/...) → "Waiting on stage" (nếu có)
→ nút Copy command. Nếu vẫn không rõ, hoặc gặp **BLOCKED** không tự giải quyết được — báo sếp,
kèm ảnh chụp màn hình Workflow.

**Gặp dòng lỗi đỏ dài (Traceback / Error) khi chạy bước 9 (Listing + A+)?** Gần như chắc chắn là
do chưa làm bước 3 (Product Truth / Phase 6A) — quay lại bước 3, làm xong rồi chạy lại bước 9.
Đây là lỗi đã biết, không phải máy hỏng.
