  Analysis: Today's Trading Performance                       
                                                              
  Results: -$120.07 realized PnL across 101 closed positions,
  636 total orders (333 buys, 303 sells)                      
  
  Problem 1: Extreme Churning (Root Cause)                    
                                                            
  The 1-minute rerank cycle combined with razor-thin          
  thresholds creates a destructive churn loop:              
                                                              
  CTRA alone had 94 orders (42 buys, 48 sells). The pattern   
  repeats every 1-2 minutes:
  1. Buy CTRA at $34.54                                       
  2. Next minute: score shifts slightly → sell signal         
  (redistribution_reduce or loss_cut)                
  3. Next minute: score qualifies again → buy back            
  4. Each round-trip loses money to spread + the 0.5% loss cut
                                                              
  Problem 2: Loss Cut at 0.5% is Inside Normal Noise          
                                                              
  loss_cut_pct: 0.005 (0.5%) is well within intraday          
  volatility. Almost every stock moves 0.5% in minutes.       
  Result: 34 loss cuts totaling -$93.72 — the biggest damage  
  source. Positions are being stopped out by normal market  
  noise, not actual adverse moves.

  Problem 3: No Minimum Hold Time

  Positions are bought and sold within the same minute.       
  There's no stabilization period for the price to settle
  after entry.                                                
                                                            
  Problem 4: Score Oscillation → "no_longer_qualifies" Churn

  Stocks hovering near the buy_threshold (60) constantly flip 
  between qualifying/disqualifying. 39 "no_longer_qualifies" 
  exits at -$45.87. No hysteresis buffer exists — score 59.9  
  triggers a sell, score 60.1 triggers a re-buy.            

  Problem 5: 303 Pending Sell Orders Never Reconciled         
  
  Sell orders are recorded as PENDING_NEW in the DB but the   
  _sync_pending_orders only reconciles buy orders. Sell orders
   pile up in the DB unreonciled, and the pipeline keeps      
  generating new sell signals for the same positions.       

  Problem 6: Redistribution Rebalances Too Aggressively       
  
  Every minute, target allocation shifts by tiny amounts,     
  generating sell/buy signals for 1-2 share differences. These
   micro-trades add up to big transaction costs.              
                                                            
  ---
  Proposed Solutions
                    
  1. Widen thresholds significantly
                                                              
  profit_take_pct: 0.03    # 3% (was 1%)
  loss_cut_pct: 0.02        # 2% (was 0.5%)                   
                                                            
  2. Add minimum hold time (e.g., 30 minutes)                 
                                                            
  Don't evaluate profit/loss or redistribution sells until a  
  position has been held for at least 30 minutes.           
                                                              
  3. Add hysteresis to qualifying threshold                 

  - Buy threshold: 60 (as-is)                                 
  - Sell threshold for "no_longer_qualifies": 55 (new)
  This prevents oscillation around the boundary.              
                                                            
  4. Increase rerank interval or add dead-band                
                                                            
  Either increase rerank_interval_minutes to 5-10, or add a   
  dead-band so redistribution only trades when allocation is
  off by >1%.                                                 
                                                            
  5. Fix sell order reconciliation                            
  
  The _reconcile_pending_buys method needs a corresponding    
  _reconcile_pending_sells or the sell order status tracking
  is broken.                                                  
                                                            
  6. Add minimum trade size

  Skip buy/sell signals where the dollar amount is trivial    
  (e.g., < $500).
                                                              
  ---                                                       
  Want me to implement these fixes? I'd suggest starting with
  the most impactful: widening thresholds (#1), adding minimum
   hold time (#2), and adding hysteresis (#3). These three
  alone would eliminate most of the churn. 