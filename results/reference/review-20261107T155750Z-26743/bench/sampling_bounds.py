"""Two-sided confidence bands for bounded cell counts (lab prototype).

Hoeffding: Maurer & Pontil (2009), Theorem 1.
Empirical Bernstein: same paper, Theorem 4, applied to Z and 1-Z.
https://www.cs.mcgill.ca/~colt2009/papers/012.pdf
Half the look alpha is allocated to each bound before intersecting their intervals.
"""
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING


def confidence_band(total, sum_squares, n, cells, width, domain, alpha):
    if n < 2 or not Decimal(0) < alpha < Decimal(1):
        raise ValueError('requires n>=2 and 0<alpha<1')
    with localcontext() as ctx:
        ctx.prec = 70
        estimate = Decimal(cells) * total / n
        variance = max(Decimal(0), (Decimal(sum_squares) - Decimal(total)**2 / n) / (n-1))
        # Hoeffding uses two tails and half the look budget: log(4/alpha).
        h_margin = Decimal(cells*width) * ((Decimal(4)/alpha).ln() / (2*n)).sqrt()
        # Theorem 4 has log(2/delta) for one tail. Two tails with delta=alpha/4 -> log(8/alpha).
        log_term = (Decimal(8)/alpha).ln()
        b_margin = Decimal(cells) * ((2*variance*log_term/n).sqrt() + Decimal(width)*7*log_term/(3*(n-1)))
        margin = min(h_margin, b_margin)
        lower = max(0, int((estimate-margin).to_integral_value(rounding=ROUND_FLOOR)))
        upper = min(domain, int((estimate+margin).to_integral_value(rounding=ROUND_CEILING)))
        relative = None if lower <= 0 else float(max(estimate-lower, upper-estimate)/lower)
        return {'estimate': str(estimate), 'lower': lower, 'upper': upper,
                'relative_error_bound': relative, 'look_alpha': str(alpha),
                'hoeffding_margin': str(h_margin), 'empirical_bernstein_margin': str(b_margin),
                'cell_sample_variance': str(variance),
                'interval_method': 'intersection with half look alpha allocated to each bound'}
