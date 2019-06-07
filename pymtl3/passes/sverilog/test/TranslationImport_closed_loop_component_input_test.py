#=========================================================================
# TranslationImport_closed_loop_component_input_test.py
#=========================================================================
# Author : Peitian Pan
# Date   : June 6, 2019
"""Closed-loop test cases for translation-import with component and input."""

from __future__ import absolute_import, division, print_function

from itertools import product
from random import randint, seed

import pytest

from pymtl3.datatypes import Bits1, Bits16, Bits32, clog2, mk_bits
from pymtl3.dsl import Component, InPort, Interface, OutPort

from ..util.test_utility import closed_loop_component_input_test

seed( 0xdeadebeef )

@pytest.mark.parametrize( "Type", [ Bits16, Bits32 ] )
def test_adder( Type ):
  def tv_in( model, test_vector ):
    model.in_1 = Type( test_vector[0] )
    model.in_2 = Type( test_vector[1] )
  class A( Component ):
    def construct( s, Type ):
      s.in_1 = InPort( Type )
      s.in_2 = InPort( Type )
      s.out = OutPort( Type )
      @s.update
      def add_upblk():
        s.out = s.in_1 + s.in_2
    def line_trace( s ): return "sum = " + str( s.out )
  test_vector = [ (randint(-255, 255), randint(-255, 255)) for _ in range(10) ]
  closed_loop_component_input_test( A( Type ), test_vector, tv_in )

@pytest.mark.parametrize("Type, n_ports", product([Bits16, Bits32], [2, 3, 4]))
def test_mux( Type, n_ports ):
  def tv_in( model, test_vector ):
    for i in xrange(n_ports):
      model.in_[i] = Type( test_vector[i] )
    model.sel = mk_bits( clog2(n_ports) )( test_vector[n_ports] )
  class A( Component ):
    def construct( s, Type, n_ports ):
      s.in_ = [ InPort( Type ) for _ in xrange(n_ports) ]
      s.sel = InPort( mk_bits( clog2(n_ports) ) )
      s.out = OutPort( Type )
      @s.update
      def add_upblk():
        s.out = s.in_[ s.sel ]
    def line_trace( s ): return "out = " + str( s.out )
  test_vector = []
  for _ in xrange(10):
    _tmp = []
    for i in xrange(n_ports):
      _tmp.append( randint(-255, 255) )
    _tmp.append( randint(0, n_ports-1) )
    test_vector.append( _tmp )
  closed_loop_component_input_test( A( Type, n_ports ), test_vector, tv_in )
