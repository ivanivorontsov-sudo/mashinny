#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>

enum { TI, TB, TS, TU, TL, TUI };

typedef struct V {
    int t;
    int sign;
    int64_t n;
    void *p;
    const char *k;
} V;

static int64_t g_steps, g_fuel = 1000000, g_depth;
static jmp_buf g_jb;
static const char *g_trap;
static int g_json, g_live;
static V **g_eff;
static int64_t g_neff, g_ceff;

static void *xm(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) { fputs("out of memory\n", stderr); exit(3); }
    return p;
}

static void trap(const char *c) { g_trap = c; longjmp(g_jb, 1); }

static void tick(int64_t n) {
    g_steps += n;
    if (g_steps > g_fuel) trap("T-FUEL");
}

static V *mk(int t) { V *v = xm(sizeof(V)); memset(v, 0, sizeof(V)); v->t = t; return v; }

static V *U_ = 0;
static V *unitv(void) { if (!U_) U_ = mk(TU); return U_; }
static V *boolv(int b) { V *v = mk(TB); v->n = b ? 1 : 0; return v; }

static int64_t norm(uint32_t *a, int64_t n) { while (n > 0 && a[n - 1] == 0) n--; return n; }

static V *bigv(int sign, uint32_t *a, int64_t n) {
    V *v = mk(TI);
    n = norm(a, n);
    v->n = n; v->p = a; v->sign = n ? sign : 0;
    return v;
}

static V *intv(int64_t x) {
    uint32_t *a = xm(8);
    uint64_t m = x < 0 ? (uint64_t)0 - (uint64_t)x : (uint64_t)x;
    a[0] = (uint32_t)m; a[1] = (uint32_t)(m >> 32);
    return bigv(x < 0 ? -1 : 1, a, 2);
}

static int64_t bitlen(V *v) {
    if (!v->n) return 0;
    uint32_t top = ((uint32_t *)v->p)[v->n - 1];
    int b = 0;
    while (top) { b++; top >>= 1; }
    return (v->n - 1) * 32 + b;
}

static V *chkbig(V *v) { if (bitlen(v) > 4096) trap("T-BIG"); return v; }

static int cmpmag(uint32_t *a, int64_t na, uint32_t *b, int64_t nb) {
    if (na != nb) return na < nb ? -1 : 1;
    for (int64_t i = na - 1; i >= 0; i--) if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
}

static int64_t addmag(uint32_t *r, uint32_t *a, int64_t na, uint32_t *b, int64_t nb) {
    uint64_t c = 0;
    int64_t n = na > nb ? na : nb;
    for (int64_t i = 0; i < n; i++) {
        c += (uint64_t)(i < na ? a[i] : 0) + (i < nb ? b[i] : 0);
        r[i] = (uint32_t)c; c >>= 32;
    }
    r[n] = (uint32_t)c;
    return n + 1;
}

static int64_t submag(uint32_t *r, uint32_t *a, int64_t na, uint32_t *b, int64_t nb) {
    int64_t br = 0;
    for (int64_t i = 0; i < na; i++) {
        int64_t d = (int64_t)a[i] - (i < nb ? b[i] : 0) - br;
        br = d < 0; if (d < 0) d += (int64_t)1 << 32;
        r[i] = (uint32_t)d;
    }
    return na;
}

static V *addsigned(int sa, uint32_t *a, int64_t na, int sb, uint32_t *b, int64_t nb) {
    int64_t m = (na > nb ? na : nb) + 1;
    uint32_t *r = xm(m * 4);
    if (!na) { memcpy(r, b, nb * 4); return bigv(sb, r, nb); }
    if (!nb) { memcpy(r, a, na * 4); return bigv(sa, r, na); }
    if (sa == sb) return bigv(sa, r, addmag(r, a, na, b, nb));
    int c = cmpmag(a, na, b, nb);
    if (c == 0) return bigv(0, r, 0);
    if (c > 0) return bigv(sa, r, submag(r, a, na, b, nb));
    return bigv(sb, r, submag(r, b, nb, a, na));
}

static V *iadd(V *x, V *y) { return addsigned(x->sign, x->p, x->n, y->sign, y->p, y->n); }
static V *isub(V *x, V *y) { return addsigned(x->sign, x->p, x->n, -y->sign, y->p, y->n); }
static V *ineg(V *x) { return bigv(-x->sign, x->p, x->n); }

static V *imul(V *x, V *y) {
    int64_t n = x->n + y->n;
    uint32_t *r = xm((n + 1) * 4);
    memset(r, 0, (n + 1) * 4);
    uint32_t *a = x->p, *b = y->p;
    for (int64_t i = 0; i < x->n; i++) {
        uint64_t c = 0;
        for (int64_t j = 0; j < y->n; j++) {
            c += (uint64_t)a[i] * b[j] + r[i + j];
            r[i + j] = (uint32_t)c; c >>= 32;
        }
        int64_t k = i + y->n;
        while (c) { c += r[k]; r[k] = (uint32_t)c; c >>= 32; k++; }
    }
    return bigv(x->sign * y->sign, r, n);
}

static void divmag(uint32_t *a, int64_t na, uint32_t *b, int64_t nb, uint32_t **q, int64_t *nq, uint32_t **r, int64_t *nr) {
    uint32_t *Q = xm((na + 1) * 4), *R = xm((nb + 2) * 4);
    memset(Q, 0, (na + 1) * 4);
    int64_t rn = 0;
    for (int64_t i = na * 32 - 1; i >= 0; i--) {
        uint32_t carry = (a[i / 32] >> (i % 32)) & 1;
        for (int64_t j = 0; j < rn; j++) { uint32_t nc = R[j] >> 31; R[j] = (R[j] << 1) | carry; carry = nc; }
        if (carry) R[rn++] = carry;
        if (cmpmag(R, rn, b, nb) >= 0) { rn = norm(R, submag(R, R, rn, b, nb)); Q[i / 32] |= (uint32_t)1 << (i % 32); }
    }
    *q = Q; *nq = norm(Q, na); *r = R; *nr = rn;
}

static void idivmod(V *x, V *y, V **qo, V **ro) {
    if (!y->n) trap("T-DIV0");
    uint32_t *q, *r; int64_t nq, nr;
    divmag(x->p, x->n, y->p, y->n, &q, &nq, &r, &nr);
    V *Q = bigv(x->sign * y->sign, q, nq), *R = bigv(x->sign, r, nr);
    if (R->n && R->sign != y->sign) { Q = isub(Q, intv(1)); R = iadd(R, y); }
    *qo = Q; *ro = R;
}

static V *idiv(V *x, V *y) { V *q, *r; idivmod(x, y, &q, &r); return q; }
static V *imod(V *x, V *y) { V *q, *r; idivmod(x, y, &q, &r); return r; }

static int icmp(V *x, V *y) {
    if (x->sign != y->sign) return x->sign < y->sign ? -1 : 1;
    int c = cmpmag(x->p, x->n, y->p, y->n);
    return x->sign < 0 ? -c : c;
}

static V *iabs(V *x) { return bigv(x->n ? 1 : 0, x->p, x->n); }

static V *iwrap64(V *x) {
    uint32_t *a = x->p;
    uint64_t m = (x->n > 0 ? a[0] : 0) | ((uint64_t)(x->n > 1 ? a[1] : 0) << 32);
    if (x->sign < 0) m = (uint64_t)0 - m;
    return intv((int64_t)m);
}

static int64_t tosmall(V *x) {
    if (x->n > 2 || bitlen(x) > 62) return x->sign < 0 ? INT64_MIN / 2 : INT64_MAX / 2;
    uint32_t *a = x->p;
    int64_t m = (int64_t)((x->n > 0 ? a[0] : 0) | ((uint64_t)(x->n > 1 ? a[1] : 0) << 32));
    return x->sign < 0 ? -m : m;
}

static V *strv(const uint32_t *s, int64_t n) {
    V *v = mk(TS);
    uint32_t *p = xm(n * 4 + 4);
    if (n) memcpy(p, s, n * 4);
    v->p = p; v->n = n;
    return v;
}

static V *strutf8(const char *s) {
    int64_t n = 0, L = (int64_t)strlen(s);
    uint32_t *b = xm(L * 4 + 4);
    const unsigned char *u = (const unsigned char *)s;
    while (*u) {
        uint32_t c = *u;
        int k = 0;
        if (c >= 0xF0) { c &= 7; k = 3; } else if (c >= 0xE0) { c &= 15; k = 2; } else if (c >= 0xC0) { c &= 31; k = 1; }
        u++;
        while (k-- > 0) { c = (c << 6) | (*u & 63); u++; }
        b[n++] = c;
    }
    return strv(b, n);
}

static V *istr(V *x) {
    char *buf = xm(x->n * 10 + 4);
    int64_t len = 0;
    uint32_t *t = xm(x->n * 4 + 4);
    memcpy(t, x->p, x->n * 4);
    int64_t n = x->n;
    while (n) {
        uint64_t r = 0;
        for (int64_t i = n - 1; i >= 0; i--) { uint64_t cur = (r << 32) | t[i]; t[i] = (uint32_t)(cur / 1000000000u); r = cur % 1000000000u; }
        n = norm(t, n);
        for (int k = 0; k < 9; k++) { buf[len++] = '0' + r % 10; r /= 10; if (!n && !r) break; }
    }
    if (!len) buf[len++] = '0';
    if (x->sign < 0) buf[len++] = '-';
    uint32_t *s = xm(len * 4 + 4);
    for (int64_t i = 0; i < len; i++) s[i] = (unsigned char)buf[len - 1 - i];
    return strv(s, len);
}

static V *iparse(const char *s) {
    int neg = *s == '-';
    if (neg) s++;
    V *r = intv(0), *ten = intv(10);
    for (; *s; s++) r = iadd(imul(r, ten), intv(*s - '0'));
    return neg ? ineg(r) : r;
}

static V *listv(V **a, int64_t n) { V *v = mk(TL); v->p = a; v->n = n; return v; }
static V **items(V *l) { return (V **)l->p; }

static int veq(V *x, V *y) {
    if (x->t == TI) return icmp(x, y) == 0;
    if (x->t == TB) return x->n == y->n;
    if (x->t == TU) return 1;
    if (x->n != y->n) return 0;
    if (x->t == TS) return memcmp(x->p, y->p, x->n * 4) == 0;
    for (int64_t i = 0; i < x->n; i++) if (!veq(items(x)[i], items(y)[i])) return 0;
    return 1;
}

static V *concat(V *x, V *y) {
    tick(x->n + y->n);
    if (x->t == TS) {
        uint32_t *p = xm((x->n + y->n) * 4 + 4);
        memcpy(p, x->p, x->n * 4); memcpy(p + x->n, y->p, y->n * 4);
        return strv(p, x->n + y->n);
    }
    V **p = xm((x->n + y->n) * sizeof(V *) + 8);
    memcpy(p, x->p, x->n * sizeof(V *)); memcpy(p + x->n, y->p, y->n * sizeof(V *));
    return listv(p, x->n + y->n);
}

static void pututf8(FILE *f, uint32_t c) {
    if (c < 0x80) fputc(c, f);
    else if (c < 0x800) { fputc(0xC0 | c >> 6, f); fputc(0x80 | (c & 63), f); }
    else if (c < 0x10000) { fputc(0xE0 | c >> 12, f); fputc(0x80 | ((c >> 6) & 63), f); fputc(0x80 | (c & 63), f); }
    else { fputc(0xF0 | c >> 18, f); fputc(0x80 | ((c >> 12) & 63), f); fputc(0x80 | ((c >> 6) & 63), f); fputc(0x80 | (c & 63), f); }
}

static void jstr(FILE *f, V *s) {
    fputc('"', f);
    uint32_t *p = s->p;
    for (int64_t i = 0; i < s->n; i++) {
        uint32_t c = p[i];
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c < 0x20) fprintf(f, "\\u%04x", c);
        else pututf8(f, c);
    }
    fputc('"', f);
}

static void jval(FILE *f, V *v) {
    if (v->t == TI) {
        V *s = istr(v);
        if (bitlen(v) >= 54) fputs("{\"int\": ", f), jstr(f, s), fputc('}', f);
        else for (int64_t i = 0; i < s->n; i++) fputc(((uint32_t *)s->p)[i], f);
    } else if (v->t == TB) fputs(v->n ? "true" : "false", f);
    else if (v->t == TU) fputs("null", f);
    else if (v->t == TS) jstr(f, v);
    else if (v->t == TL) {
        fputc('[', f);
        for (int64_t i = 0; i < v->n; i++) { if (i) fputs(", ", f); jval(f, items(v)[i]); }
        fputc(']', f);
    } else {
        fprintf(f, "{\"ui\": \"%s\", \"args\": [", v->k);
        for (int64_t i = 0; i < v->n; i++) { if (i) fputs(", ", f); jval(f, items(v)[i]); }
        fputs("]}", f);
    }
}

static V *p_print(V *s) {
    if (g_neff == g_ceff) { g_ceff = g_ceff * 2 + 8; V **n = xm(g_ceff * sizeof(V *)); if (g_neff) memcpy(n, g_eff, g_neff * sizeof(V *)); g_eff = n; }
    g_eff[g_neff++] = s;
    if (g_live) {
        for (int64_t i = 0; i < s->n; i++) pututf8(stdout, ((uint32_t *)s->p)[i]);
        fputc('\n', stdout); fflush(stdout);
    }
    return unitv();
}

static V *p_get(V *l, V *i) {
    int64_t k = tosmall(i);
    if (k < 0 || k >= l->n) trap("T-INDEX");
    return items(l)[k];
}

static V *p_push(V *l, V *x) {
    tick(l->n);
    V **p = xm((l->n + 1) * sizeof(V *));
    memcpy(p, l->p, l->n * sizeof(V *)); p[l->n] = x;
    return listv(p, l->n + 1);
}

static V *p_slice(V *s, V *a, V *b) {
    int64_t lo = tosmall(a), hi = tosmall(b), n = s->n;
    if (lo > n) lo = n; if (lo < 0) lo = 0;
    if (hi > n) hi = n; if (hi < lo) hi = lo;
    tick(hi - lo);
    if (s->t == TS) return strv((uint32_t *)s->p + lo, hi - lo);
    V **p = xm((hi - lo) * sizeof(V *) + 8);
    memcpy(p, items(s) + lo, (hi - lo) * sizeof(V *));
    return listv(p, hi - lo);
}

static V *p_rev(V *s) {
    tick(s->n);
    if (s->t == TS) {
        uint32_t *p = xm(s->n * 4 + 4);
        for (int64_t i = 0; i < s->n; i++) p[i] = ((uint32_t *)s->p)[s->n - 1 - i];
        return strv(p, s->n);
    }
    V **p = xm(s->n * sizeof(V *) + 8);
    for (int64_t i = 0; i < s->n; i++) p[i] = items(s)[s->n - 1 - i];
    return listv(p, s->n);
}

static V *p_range(V *a, V *b) {
    V *d = isub(b, a);
    int64_t k = d->sign > 0 ? tosmall(d) : 0;
    tick(k);
    V **p = xm(k * sizeof(V *) + 8);
    V *one = intv(1), *c = a;
    for (int64_t i = 0; i < k; i++) { p[i] = c; c = iadd(c, one); }
    return listv(p, k);
}

static V *p_sum(V *l) {
    tick(l->n);
    V *s = intv(0);
    for (int64_t i = 0; i < l->n; i++) s = iadd(s, items(l)[i]);
    return chkbig(s);
}

static int cmpv(const void *a, const void *b) { return icmp(*(V **)a, *(V **)b); }

static V *p_sort(V *l) {
    tick(l->n);
    V **p = xm(l->n * sizeof(V *) + 8);
    memcpy(p, l->p, l->n * sizeof(V *));
    for (int64_t i = 1; i < l->n; i++) { V *x = p[i]; int64_t j = i - 1; while (j >= 0 && icmp(p[j], x) > 0) { p[j + 1] = p[j]; j--; } p[j + 1] = x; }
    return listv(p, l->n);
}

static V *p_join(V *l, V *sep) {
    tick(l->n);
    int64_t n = 0;
    for (int64_t i = 0; i < l->n; i++) n += items(l)[i]->n + (i ? sep->n : 0);
    uint32_t *p = xm(n * 4 + 4), *q = p;
    for (int64_t i = 0; i < l->n; i++) {
        if (i) { memcpy(q, sep->p, sep->n * 4); q += sep->n; }
        memcpy(q, items(l)[i]->p, items(l)[i]->n * 4); q += items(l)[i]->n;
    }
    return strv(p, n);
}

static V *p_chars(V *s) {
    tick(s->n);
    V **p = xm(s->n * sizeof(V *) + 8);
    for (int64_t i = 0; i < s->n; i++) p[i] = strv((uint32_t *)s->p + i, 1);
    return listv(p, s->n);
}

static V *mkui(const char *k, int n, V **a) {
    V *v = mk(TUI);
    v->k = k; v->n = n;
    V **p = xm(n * sizeof(V *)); memcpy(p, a, n * sizeof(V *));
    v->p = p;
    if (!strcmp(k, "col") || !strcmp(k, "row") || !strcmp(k, "stack")) tick(a[n - 1]->n);
    return v;
}

static V *bmul(V *x, V *y) { if (bitlen(x) + bitlen(y) > 4097) trap("T-BIG"); return chkbig(imul(x, y)); }

static void enter(void) { if (++g_depth > 10000) trap("T-DEPTH"); }

static V *grow(V *l, int64_t *cap, V *x) {
    if (l->n == *cap) { *cap = *cap * 2 + 4; V **p = xm(*cap * sizeof(V *)); if (l->n) memcpy(p, l->p, l->n * sizeof(V *)); l->p = p; }
    items(l)[l->n++] = x;
    return l;
}

static void finish(int ok, V *val) {
    if (g_json) {
        printf("{\"status\": \"%s\", ", ok ? "ok" : "trap");
        if (ok) { fputs("\"value\": ", stdout); jval(stdout, val); }
        else printf("\"trap\": \"%s\"", g_trap);
        printf(", \"steps\": %lld, \"effects\": [", (long long)g_steps);
        for (int64_t i = 0; i < g_neff; i++) { if (i) fputs(", ", stdout); jstr(stdout, g_eff[i]); }
        fputs("]}\n", stdout);
    } else if (!ok) {
        fprintf(stderr, "trap: %s\n", g_trap);
    }
}
