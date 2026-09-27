# Elastic Agent 8.19.16

Extension cài Elastic Agent trên các máy Windows thuộc nhóm inventory `domain`,
đăng ký vào Fleet Server đang có. Không tạo thêm VM hay Fleet Server.

## Sử dụng trong GOAD console

Load instance cần cài, sau đó chạy:

```text
install_extension elastic_agent
```

Console hỏi IP/hostname Fleet Server, enrollment token (ẩn khi nhập), phiên bản
(mặc định `8.19.16`) và đường dẫn CA `.crt` trên máy đang chạy GOAD.
Hoặc truyền trực tiếp các tùy chọn:

```text
install_extension elastic_agent --ip 192.168.50.10 --token YOUR_ENROLLMENT_TOKEN --crt "/path/to/elk/certs/ca/ca.crt" --version 8.19.16
```

Có thể bỏ `--token` để nhập token ẩn, tránh lưu token vào lịch sử lệnh:

```text
install_extension elastic_agent --ip 192.168.50.10 --crt "/path/to/elk/certs/ca/ca.crt" --version 8.19.16
```

IP đơn được chuyển thành `https://IP:8220`; có thể nhập
`https://fleet.example.com:8220` để dùng tên DNS trong chứng chỉ.
Chỉ dùng **Fleet enrollment token của policy Windows**, không dùng service token
của Fleet Server hoặc enrollment token Elasticsearch/Kibana.

`set_extensions elastic_agent` chỉ chọn extension cho lần tạo lab; phần nhập
cấu hình xuất hiện khi bước `install_extension` bắt đầu.

## CA và TLS

- `--crt` là đường dẫn **local**, hỗ trợ dấu cách khi đặt trong dấu nháy.
- File phải là CA công khai dạng PEM (`BEGIN CERTIFICATE`), không phải private key
  hoặc chỉ chứng chỉ máy chủ. Có thể chứa một chuỗi chứng chỉ CA.
- Nội dung CA được lưu trong `workspace/<instance>/elastic_agent_options.yml`
  cùng cấu hình enrollment. File có quyền `0600` trên hệ điều hành POSIX và chứa
  token dạng rõ để GOAD có thể chạy lại; bảo vệ cả bản sao workspace/jumpbox.
- CA được chép tới `C:\ProgramData\Elastic\certs\elastic-ca.crt` trước enrollment.
  Playbook dùng `--certificate-authorities`; không dùng `--insecure` và không
  thêm CA vào kho tin cậy toàn hệ thống Windows.
- IP hoặc tên DNS phải có trong SAN của chứng chỉ Fleet Server; thời gian của
  máy Windows phải đúng và CA/chứng chỉ phải còn hạn. File CA đúng không thể sửa
  lỗi tên chứng chỉ không khớp.
- Máy Windows cần truy cập Fleet Server (thường `8220`), Elasticsearch output
  (thường `9200`) và HTTPS `artifacts.elastic.co` để tải agent/checksum SHA512.

## Lưu ý riêng cho profile dedicated-es-disk

Profile hiện dùng output mặc định `https://shared-elasticsearch:9200` với CA
`/etc/elastic-agent/certs/elastic-ca.crt`. Đây là cấu hình cho container Linux.
Trước khi lấy enrollment token, tạo/chọn policy Windows với output Elasticsearch
có địa chỉ mà GOAD truy cập được, chẳng hạn `https://192.168.50.10:9200`
**nếu đó là địa chỉ triển khai thực tế và có trong SAN chứng chỉ Elasticsearch**.
Đặt Advanced YAML của output Windows:

```yaml
ssl.certificate_authorities:
  - 'C:\ProgramData\Elastic\certs\elastic-ca.crt'
ssl.verification_mode: full
```

Gán output đó cho cả dữ liệu và monitoring của policy Windows. Cách này áp dụng
khi Fleet và Elasticsearch dùng cùng CA như profile đã kiểm tra; nếu khác CA,
file cung cấp phải chứa cả hai CA. Giữ output Linux riêng cho Fleet Server.
Thêm các integration thu thập Windows/System/Sysmon cần thiết vào policy;
extension không tự sửa cấu hình Kibana hoặc tạo enrollment token.

## Chạy lại và xác minh

```text
provision_extension elastic_agent
```

Lệnh này dùng cấu hình đã lưu, không hỏi lại. Chạy `install_extension` với các
tùy chọn mới để thay IP/token/CA; agent hiện có sẽ được enroll lại nếu fingerprint
cấu hình thay đổi. Cấu hình giống nhau không cài hoặc enroll trùng. Thay phiên bản
trong tùy chọn chỉ ảnh hưởng cài mới; nâng cấp agent đã cài qua Fleet.

Playbook kiểm tra service, trạng thái agent và kết nối Fleet; file đánh dấu cấu
hình chỉ được ghi sau khi kiểm tra thành công. Nếu trạng thái chưa healthy,
playbook báo lỗi và giữ file tải để lần sau chạy tiếp. Cần kiểm tra thêm agent
trong Fleet và log thực tế trong Discover để xác nhận toàn bộ đường truyền dữ liệu.
Khi lệnh cài/enroll trả lỗi, Ansible hiển thị exit code cùng stdout/stderr đã
lọc enrollment token; tham số lệnh gốc tiếp tục được che bằng `no_log`.

`ansible/uninstall.yml` hỗ trợ gỡ agent và kiểm tra service đã mất. Nếu bật
uninstall protection trong policy, cần xử lý bằng quy trình uninstall token
của Elastic trước; extension không tự tắt bảo vệ.

## Kiểm thử phát triển

```sh
python -m unittest discover -s tests -p test_elastic_agent_extension.py -v
ansible-playbook -i ad/GOAD/data/inventory -i extensions/elastic_agent/inventory extensions/elastic_agent/ansible/install.yml --syntax-check
ansible-playbook -i ad/GOAD/data/inventory -i extensions/elastic_agent/inventory extensions/elastic_agent/ansible/uninstall.yml --syntax-check
```

Tham chiếu: [lệnh Elastic Agent](https://www.elastic.co/guide/en/fleet/8.19/elastic-agent-cmd-options.html),
[TLS cho Fleet Server](https://www.elastic.co/guide/en/fleet/8.19/secure-connections.html).
