#include<bits/stdc++.h>
using namespace std;
using ll = long long;

int main(){
    ios::sync_with_stdio(false);
    cin.tie(0);
    int T;
    cin >> T;
    while(T--){
        ll n, m;
        cin >> n >> m;
        // Feasibility: m weights (subset sums) can cover at most 2^m - 1.
        if(m <= 30){
            ll maxSum = (1LL << m) - 1;
            if(n > maxSum){
                cout << -1 << '\n';
                continue;
            }
        }
        // Binary search the minimum possible heaviest weight M.
        // With every weight <= M, the best "complete chain" configuration is
        // a_i = min(M, 2^{i-1}), giving total sum f(M) = sum min(M, 2^{i-1}).
        // Need f(M) >= n.
        ll lo = 1, hi = n, ans = n;
        while(lo <= hi){
            ll mid = (lo + hi) / 2;
            ll lg = 63 - __builtin_clzll(mid); // floor(log2(mid))
            ll k = min(m, lg + 1);             // #terms with 2^{i-1} <= M
            ll total = (1LL << k) - 1 + (m - k) * mid;
            if(total >= n){
                ans = mid;
                hi = mid - 1;
            } else {
                lo = mid + 1;
            }
        }
        cout << ans << '\n';
    }
    return 0;
}