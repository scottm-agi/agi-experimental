import os
import ipaddress
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from python.helpers.print_style import PrintStyle
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from datetime import datetime, timedelta, timezone

def generate_self_signed_cert(cert_path="/agix/cert.pem", key_path="/agix/key.pem", common_name="localhost"):
    from python.helpers.files import fix_dev_path
    cert_path_abs = fix_dev_path(cert_path)
    key_path_abs = fix_dev_path(key_path)

    if os.path.exists(cert_path_abs) and os.path.exists(key_path_abs):
        return

    PrintStyle().info(f"Generating self-signed SSL certificate for {common_name}...")
    
    try:
        # Generate private key
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # Generate certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.now(timezone.utc)
        ).not_valid_after(
            datetime.now(timezone.utc) + timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(common_name),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        ).add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        ).sign(key, hashes.SHA256())

        # Ensure directory exists
        os.makedirs(os.path.dirname(cert_path_abs), exist_ok=True)

        # Write private key
        with open(key_path_abs, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))

        # Write certificate
        with open(cert_path_abs, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        
        PrintStyle().success(f"SSL certificate generated at {cert_path_abs}")
    except Exception as e:
        PrintStyle().error(f"Failed to generate SSL certificate: {e}")
        # Dont re-raise, run_ui will handle ssl_context being None

if __name__ == "__main__":
    generate_self_signed_cert()
