# Authentication

## 1. Overview

The ERP System uses JWT-based authentication.

Authentication determines the identity of the user. Authorization and permissions are handled separately by the RBAC system.

The authentication flow supports:

- User registration
- Login
- Password verification
- Access tokens
- Refresh tokens
- OAuth2 Bearer authentication
- Protected API endpoints

---

## 2. Password Security

Passwords are never stored in plain text.

The system uses:

```text
pwdlib
+
Argon2
```

Password flow:

```text
User Password
     │
     ▼
Argon2 Hash
     │
     ▼
users.password_hash
```

Example stored value:

```text
$argon2id$...
```

During login, the submitted password is verified against the stored hash.

---

## 3. JWT Tokens

The authentication system issues two token types:

```text
Access Token
Refresh Token
```

### Access Token

Used for normal authenticated API requests.

Example payload structure:

```json
{
  "sub": "123",
  "type": "access",
  "exp": 1234567890
}
```

`sub` contains the user ID.

`type` identifies the token as an access token.

`exp` defines its expiration time.

### Refresh Token

Used to obtain a new access token without requiring the user to enter their password again.

Refresh tokens have a longer lifetime than access tokens.

---

## 4. Authentication Flow

```text
Email + Password
       │
       ▼
POST /auth/token
       │
       ▼
Verify User
       │
       ▼
Verify Password
       │
       ▼
Access Token + Refresh Token
       │
       ▼
Client Application
       │
       ▼
Authorization: Bearer <access_token>
       │
       ▼
Protected ERP API
```

---

## 5. Current User Resolution

Protected endpoints use the authentication dependency responsible for resolving the current user.

Conceptually:

```text
Bearer Token
    │
    ▼
Decode JWT
    │
    ▼
Read sub
    │
    ▼
Find User
    │
    ▼
Current User
```

An invalid, missing or expired token must not provide authenticated access.

---

## 6. OAuth2 and Swagger

FastAPI exposes OAuth2 authentication through Swagger UI.

The token endpoint is:

```text
/api/v1/auth/token
```

Swagger can authenticate using the user's email as the username and the user's password.

After authorization, Swagger automatically sends:

```text
Authorization: Bearer <token>
```

to protected endpoints.

---

## 7. Registration

The registration API creates a new ERP user.

Registration includes data such as:

```text
email
password
first_name
last_name
```

Before the user is stored:

```text
plain password
      │
      ▼
Argon2
      │
      ▼
password_hash
```

Duplicate email addresses are rejected.

---

## 8. Authentication vs Authorization

Authentication answers:

```text
Who is the user?
```

Authorization answers:

```text
What is the user allowed to do?
```

Example:

```text
JWT
 │
 ▼
User 15
 │
 ▼
Company 1
 │
 ▼
Accountant
 │
 ▼
accounts.create
```

JWT authentication alone does not grant permission to company resources.

The RBAC system performs the authorization check.

---

## 9. Security Principles

The authentication subsystem follows these rules:

1. Passwords are never stored in plain text.
2. Passwords are hashed using Argon2.
3. JWT secrets must not be committed to Git.
4. Authentication secrets belong in environment configuration.
5. Access tokens should have limited lifetimes.
6. Refresh tokens should have longer but controlled lifetimes.
7. Protected endpoints must validate access tokens.
8. Authorization must still be checked after authentication.
9. Client applications must not be trusted to enforce permissions.
10. Sensitive authentication errors should not reveal unnecessary information.

---

## 10. Future Improvements

Planned authentication improvements may include:

```text
Refresh token rotation
Token revocation
Session/device management
Password reset
Email verification
Login audit history
Brute-force protection
Rate limiting
Optional multi-factor authentication
```

These features will be added as the security architecture evolves.