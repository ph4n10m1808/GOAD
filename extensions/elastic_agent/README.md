# Elastic Agent 9.5.6

Extension cài Elastic Agent trên các máy Windows thuộc nhóm inventory `domain`,
đăng ký vào Fleet Server đang có. Không tạo thêm VM hay Fleet Server.

## One CLI command for an existing lab

The CLI task `install_extension` runs repeated `-e` selections in order and exits nonzero on the first failed installer. With `-i`, it loads that exact existing instance; it does not create/rebuild VMs. Existing extension inventories, including Exchange, are retained. Install Sysmon first, then Elastic Agent:

```bash
./goad.sh -t install_extension -i INSTANCE_ID -e sysmon -e elastic_agent \
  --fleet-url https://fleet.example.com:8220 --fleet-ca /path/to/public-ca.crt --agent-version 9.5.6
```

The Windows policy enrollment token is requested through the existing hidden prompt. Never use the Fleet Server service token or the Zeek policy token. This prepares/runs the same native extension playbooks as the console; no separate installer or Windows service patch is introduced.

## Sử dụng trong GOAD console

Load instance cần cài, sau đó chạy:

```text
install_extension elastic_agent
```

Console hỏi IP/hostname Fleet Server, enrollment token (ẩn khi nhập), phiên bản
(mặc định `9.5.6`) và đường dẫn CA `.crt` trên máy đang chạy GOAD.
Truyền URL và CA nếu cần; bỏ `--token` để nhập token ẩn, tránh lưu secret trong lịch sử lệnh:

```text
install_extension elastic_agent --ip https://fleet.example.com:8220 --crt "/path/to/public-ca.crt" --version 9.5.6
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

## Output và policy Windows

Dùng token riêng của policy `soc-goad-windows`. Output Fleet phải trỏ đến HTTPS Elasticsearch mà GOAD truy cập được, có SAN chứng thư phù hợp. Cấu hình hiện tại nhúng CA công khai vào output và dùng xác minh đầy đủ, không yêu cầu Windows đọc file từ thư mục SIEM. CA local của bước enrollment nằm ở `C:\ProgramData\Elastic\certs\elastic-ca.crt`.

Policy native đã có System, Windows/Sysmon/PowerShell, Defend Detect, IIS, MSSQL và các channel vận hành bổ sung. SQL Audit hiện ghi vào Application event33205; System Application loại riêng event này vì MSSQL đã thu thập. AD Entity Analytics đang tắt chờ tài khoản/CA LDAPS và một collector duy nhất. Zeek dùng policy/token khác.

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

## Native logging and service identities — 14/11/2026

Elastic Agent dùng archive chính thức, giữ kiểm SHA512, không vá binary. Audit/channel log được cấu hình bằng lệnh Windows native, không tạo backup mới trên GOAD theo yêu cầu hiện tại. Cấu hình DNS forwarder GOAD-Light hỗ trợ danh sách phân cách bằng dấu phẩy; dữ liệu lab dùng hai upstream đã kiểm chứng, không thay DNS client AD.

Hai tài khoản AD `soc_siem` và `soc_soar` được chuẩn bị tự động trong bước cuối của Ansible GOAD-Light trên DC cha: SIEM chỉ đọc AD bằng quyền Domain Users; SOAR tắt, chưa có quyền ứng phó. Role `settings/soc_accounts` lưu mật khẩu sinh một lần ở thư mục riêng `.soc-credentials/` của instance, dùng sensitive parameters/no_log; guest chỉ giữ SID/GUID và fingerprint để tránh tự nhận/reset tài khoản có sẵn. Tài khoản tên cũ dùng dấu gạch ngang sẽ chặn tạo mới để người vận hành quyết định migration. Đợt chuẩn bị14/11 là source-only. Readback17/11 đã xác minh tài khoản trên lab dựng lại và bốn Agent ACK75; đây không phải bảo đảm mọi lần dựng hoặc mọi nguồn log đều đã được nghiệm thu.
