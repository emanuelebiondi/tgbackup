"""
===============================================================================
Project      : TGBackup
File         : tgbackup/crypto.py
Description  : Symmetric cryptographic module for TGBackup.
Purpose      : Implements authenticated AES-256-GCM encryption and decryption with
               key derivation using PBKDF2-HMAC-SHA256, ensuring confidentiality
               and integrity of backup chunks uploaded to Telegram.
===============================================================================
"""

import os
from typing import BinaryIO
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# =============================================================================
# Cryptographic Constants
# =============================================================================
# Magic number identifying TGBackup encrypted files (Version 1)
MAGIC_HEADER = b"TGB1"

# Salt size in bytes for key derivation
SALT_SIZE = 16

# Nonce/IV size in bytes for AES-GCM (12 bytes NIST recommendation)
NONCE_SIZE = 12

# PBKDF2 iteration count to mitigate brute-force attacks
PBKDF2_ITERATIONS = 100_000


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """
    ---------------------------------------------------------------------------
    Function: derive_key
    Description:
        Derives a 256-bit (32-byte) symmetric encryption key from an arbitrary
        passphrase and a random salt using the standard PBKDF2-HMAC-SHA256.
    
    Input parameters:
        @param passphrase (str) : Secret passphrase provided by the user.
        @param salt (bytes)      : Random byte buffer (SALT_SIZE) to defend
                                  against rainbow table attacks.
    
    Return value:
        @return (bytes)         : Derived 32-byte (256-bit) encryption key.
    ---------------------------------------------------------------------------
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    """
    ---------------------------------------------------------------------------
    Function: encrypt_bytes
    Description:
        Encrypts a plaintext byte block using authenticated AES-256-GCM.
        Generates a unique salt and nonce per invocation, producing the binary layout:
        [MAGIC_HEADER (4B)] + [SALT (16B)] + [NONCE (12B)] + [CIPHERTEXT + AUTH_TAG].
    
    Input parameters:
        @param data (bytes)     : Plaintext binary data to encrypt.
        @param passphrase (str) : Passphrase used to encrypt the payload.
    
    Return value:
        @return (bytes)         : Binary buffer containing header, salt, nonce,
                                  and ciphertext with GCM authentication tag.
    ---------------------------------------------------------------------------
    """
    # Generate unique cryptographic random values for this block
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    
    # Derive symmetric key from user passphrase
    key = derive_key(passphrase, salt)
    
    # Initialize Galois/Counter Mode cipher
    aesgcm = AESGCM(key)
    
    # Encrypt data with automatic 16-byte authentication tag appending
    ciphertext = aesgcm.encrypt(nonce, data, None)
    
    # Pack final binary payload
    return MAGIC_HEADER + salt + nonce + ciphertext


def decrypt_bytes(encrypted_data: bytes, passphrase: str) -> bytes:
    """
    ---------------------------------------------------------------------------
    Function: decrypt_bytes
    Description:
        Validates integrity and decrypts an encrypted binary package.
        Checks the magic header, extracts salt and nonce, derives the key,
        and decrypts the ciphertext. If modified or if passphrase is wrong,
        an authentication error is raised by AES-GCM.
    
    Input parameters:
        @param encrypted_data (bytes) : Encrypted binary package to validate & decrypt.
        @param passphrase (str)      : Passphrase originally used for encryption.
    
    Return value:
        @return (bytes)              : Decrypted plaintext data.
    
    Exceptions raised:
        @raises ValueError           : If payload length is invalid or magic header fails.
        @raises InvalidTag           : If authentication fails (wrong key or tampering).
    ---------------------------------------------------------------------------
    """
    min_required_len = len(MAGIC_HEADER) + SALT_SIZE + NONCE_SIZE + 16
    if len(encrypted_data) < min_required_len:
        raise ValueError("Invalid encrypted data: buffer shorter than minimum header size.")
    
    # 1. Verify Magic Header
    magic = encrypted_data[:len(MAGIC_HEADER)]
    if magic != MAGIC_HEADER:
        raise ValueError(f"Invalid file header: expected '{MAGIC_HEADER.decode()}', received '{magic}'.")
    
    # 2. Extract packet components
    offset = len(MAGIC_HEADER)
    salt = encrypted_data[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = encrypted_data[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext = encrypted_data[offset:]
    
    # 3. Derive key and perform authenticated decryption
    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def encrypt_file(input_path: str, output_path: str, passphrase: str):
    """
    ---------------------------------------------------------------------------
    Function: encrypt_file
    Description:
        Reads a plaintext file from filesystem, encrypts it with AES-256-GCM,
        and writes the resulting ciphertext to the specified destination.
    
    Input parameters:
        @param input_path (str)  : Source plaintext file path.
        @param output_path (str) : Destination ciphertext file path.
        @param passphrase (str)  : Encryption passphrase.
    
    Return value:
        @return None
    ---------------------------------------------------------------------------
    """
    with open(input_path, "rb") as f_in:
        raw_data = f_in.read()
    encrypted = encrypt_bytes(raw_data, passphrase)
    with open(output_path, "wb") as f_out:
        f_out.write(encrypted)


def decrypt_file(input_path: str, output_path: str, passphrase: str):
    """
    ---------------------------------------------------------------------------
    Function: decrypt_file
    Description:
        Reads an encrypted file from filesystem, verifies integrity, decrypts it,
        and saves plaintext output to the destination path.
    
    Input parameters:
        @param input_path (str)  : Source ciphertext file path.
        @param output_path (str) : Destination plaintext file path.
        @param passphrase (str)  : Passphrase with which the file was encrypted.
    
    Return value:
        @return None
    ---------------------------------------------------------------------------
    """
    with open(input_path, "rb") as f_in:
        encrypted_data = f_in.read()
    decrypted = decrypt_bytes(encrypted_data, passphrase)
    with open(output_path, "wb") as f_out:
        f_out.write(decrypted)
