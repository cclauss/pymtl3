"""
========================================================================
EnqDeqIfc.py
========================================================================
RTL implementation of deq and enq interface.

Author: Yanghui Ou
  Date: Mar 21, 2019
"""
from pymtl3 import *
from pymtl3.stdlib.ifcs import GiveIfcFL, GiveIfcRTL, RecvIfcFL, RecvIfcRTL

#-------------------------------------------------------------------------
# EnqIfcRTL
#-------------------------------------------------------------------------

# class EnqIfcRTL( RecvIfcRTL ):
#   pass
EnqIfcRTL = RecvIfcRTL

#-------------------------------------------------------------------------
# DeqIfcRTL
#-------------------------------------------------------------------------

# class DeqIfcRTL( GiveIfcRTL ):
#   pass
DeqIfcRTL = GiveIfcRTL

class EnqIfcFL( RecvIfcFL ):
  pass

class DeqIfcFL( GiveIfcFL ):
  pass
