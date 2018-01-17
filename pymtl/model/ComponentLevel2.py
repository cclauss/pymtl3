#=========================================================================
# ComponentLevel2.py
#=========================================================================

from NamedObject     import NamedObject
from ComponentLevel1 import ComponentLevel1
from Connectable     import Signal, InVPort, OutVPort, Wire, Const, Interface
from ConstraintTypes import U, RD, WR, ValueConstraint
from collections     import defaultdict
from errors import InvalidConstraintError, SignalTypeError, \
                   MultiWriterError, VarNotDeclaredError, InvalidFuncCallError, UpblkFuncSameNameError
import AstHelper

import inspect2, re, ast, gc
p = re.compile('( *(@|def))')

class ComponentLevel2( ComponentLevel1 ):

  #-----------------------------------------------------------------------
  # Private methods
  #-----------------------------------------------------------------------

  def __new__( cls, *args, **kwargs ):
    inst = super( ComponentLevel2, cls ).__new__( cls, *args, **kwargs )

    inst._update_on_edge = set()

    # constraint[var] = (sign, func)
    inst._RD_U_constraints = defaultdict(set)
    inst._WR_U_constraints = defaultdict(set)
    inst._name_func = {}

    return inst

  def _cache_func_meta( s, func ):
    """ Convention: the source of a function/update block across different
    instances should be the same. You can construct different functions
    based on the condition, but please use different names. This not only
    keeps the caching valid, but also make the code more readable.

    According to the convention, we can cache the information of a
    function in the *class object* to avoid redundant parsing. """
    cls = type(s)
    try:
      name_src = cls._name_src
      name_ast = cls._name_ast
      name_rd  = cls._name_rd
      name_wr  = cls._name_wr
      name_fc  = cls._name_fc
    except:
      name_src = cls._name_src = {}
      name_ast = cls._name_ast = {}
      name_rd  = cls._name_rd  = {}
      name_wr  = cls._name_wr  = {}
      name_fc  = cls._name_fc  = {}

    name = func.__name__
    if name not in name_src:
      name_src[ name ] = src  = p.sub( r'\2', inspect2.getsource(func) )
      name_ast[ name ] = tree = ast.parse( src )
      name_rd[ name ]  = rd   = []
      name_wr[ name ]  = wr   = []
      name_fc[ name ]  = fc   = []
      AstHelper.extract_reads_writes_calls( func, tree, rd, wr, fc )

  # Override
  def _declare_vars( s ):
    super( ComponentLevel2, s )._declare_vars()

    s._all_update_on_edge = set()

    s._all_RD_U_constraints = defaultdict(set)
    s._all_WR_U_constraints = defaultdict(set)

    # We don't collect func's metadata
    # because every func is local to the component
    s._all_upblk_reads  = {}
    s._all_upblk_writes = {}
    s._all_upblk_calls  = {}

  def _elaborate_read_write_func( s ):

    # We have parsed AST to extract every read/write variable name.
    # I refactor the process of materializing objects in this function
    # Pass in the func as well for error message

    def extract_obj_from_names( func, names ):

      def expand_array_index( obj, name_depth, node_depth, idx_depth, idx, obj_list ):
        """ Find s.x[0][*][2], if index is exhausted, jump back to lookup_var """

        if idx_depth >= len(idx): # exhausted, go to next level of name
          lookup_var( obj, name_depth+1, node_depth+1, obj_list )

        elif idx[ idx_depth ] == "*": # special case, materialize all objects
          if isinstance( obj, Signal ): # Signal[*] is the signal itself
            add_all( obj, obj_list, node_depth )
          else:
            for i, o in enumerate( obj ):
              expand_array_index( o, name_depth, node_depth, idx_depth+1, idx, obj_list )
        else:
          _index = idx[ idx_depth ]
          try:
            index = int( _index ) # handle x[2]'s case
            expand_array_index( obj[index], name_depth, node_depth, idx_depth+1, idx, obj_list )
          except TypeError: # cannot convert to integer
            if not isinstance( _index, slice ):
              raise VarNotDeclaredError( obj, _index, func, s, nodelist[node_depth].lineno )
            expand_array_index( obj[_index], name_depth, node_depth, idx_depth+1, idx, obj_list )
          except IndexError:
            pass

      def add_all( obj, obj_list, node_depth ):
        """ Already found, but it is an array of objects,
            s.x = [ [ A() for _ in xrange(2) ] for _ in xrange(3) ].
            Recursively collect all signals. """
        if   isinstance( obj, Signal ):
          obj_list.add( obj )
        elif isinstance( obj, list ): # SORRY
          for i, o in enumerate( obj ):
            add_all( o, obj_list, node_depth )

      def lookup_var( obj, name_depth, node_depth, obj_list ):
        """ Look up the object s.a.b.c in s. Jump to expand_array_index if c[] """

        if name_depth >= len(obj_name): # exhausted
          if not callable(obj): # exclude function calls
            add_all( obj, obj_list, node_depth ) # if this object is a list/array again...
          return
        else:
          field, idx = obj_name[ name_depth ]
          try:
            obj = getattr( obj, field )
          except AttributeError:
            raise VarNotDeclaredError( obj, field, func, s, nodelist[node_depth].lineno )

          if not idx: lookup_var( obj, name_depth+1, node_depth+1, obj_list )
          else:       expand_array_index( obj, name_depth, node_depth+1, 0, idx, obj_list )

      """ extract_obj_from_names:
      Here we enumerate names and use the above functions to turn names
      into objects """

      all_objs = set()

      for obj_name, nodelist in names:
        print obj_name, nodelist
        objs = set()

        if obj_name[0][0] == "s":
          lookup_var( s, 1, 1, objs )
          all_objs |= objs

      return all_objs

    def extract_call_from_names( func, names, name_func ):
      """ extract_calls_from_names:
      Here we turn name into function calls """

      all_calls = set()

      for obj_name, nodelist in names:
        call = None

        # This is some instantiation I guess. TODO only support one layer

        if obj_name[0][0] == "s" and len(obj_name) == 2:
          try:
            call = getattr( s, obj_name[1][0] )
          except AttributeError as e:
            raise VarNotDeclaredError( call, obj_name[1][0], func, s, nodelist[-1].lineno )

        # This is a function call without "s." prefix, check func list
        elif obj_name[0][0] in name_func:
          call = name_func[ obj_name[0][0] ]
          all_calls.add( call )

      return all_calls

    """ elaborate_read_write_func """

    # Access cached data in this component

    cls = s.__class__
    try:
      name_rd, name_wr, name_fc = cls._name_rd, cls._name_wr, cls._name_fc
    except AttributeError: # This component doesn't have update block
      pass

    # what object each astnode corresponds to. You can't have two update
    # blocks in one component that have the same ast.
    s._astnode_objs = defaultdict(list) 
    s._func_reads  = {}
    s._func_writes = {}
    s._func_calls  = {}
    for name, func in s._name_func.iteritems():
      s._func_reads [ func ] = extract_obj_from_names( func, name_rd[ name ] )
      s._func_writes[ func ] = extract_obj_from_names( func, name_wr[ name ] )
      s._func_calls [ func ] = extract_call_from_names( func, name_fc[ name ], s._name_func )

    s._upblk_reads  = {}
    s._upblk_writes = {}
    s._upblk_calls  = {}
    for name, blk in s._name_upblk.iteritems():
      s._upblk_reads [ blk ] = extract_obj_from_names( blk, name_rd[ name ] )
      s._upblk_writes[ blk ] = extract_obj_from_names( blk, name_wr[ name ] )
      s._upblk_calls [ blk ] = extract_call_from_names( blk, name_fc[ name ], s._name_func )

  # Override
  def _collect_vars( s, m ):
    super( ComponentLevel2, s )._collect_vars( m )

    if isinstance( m, ComponentLevel2 ):
      s._all_update_on_edge |= m._update_on_edge

      for k in m._RD_U_constraints:
        s._all_RD_U_constraints[k] |= m._RD_U_constraints[k]
      for k in m._WR_U_constraints:
        s._all_WR_U_constraints[k] |= m._WR_U_constraints[k]

      # I assume different update blocks will always have different ids
      s._all_upblk_reads.update( m._upblk_reads )
      s._all_upblk_writes.update( m._upblk_writes )

      for blk, calls in m._upblk_calls.iteritems():
        s._all_upblk_calls[ blk ] = calls

        for call in calls:

          # Expand function calls. E.g. upA calls fx, fx calls fy and fz
          # This is invalid: fx calls fy but fy also calls fx
          # To detect this, we need to use dfs and see if the current node
          # has an edge to a previously marked ancestor

          def dfs( u, stk ):

            # Add all read/write of funcs to the outermost upblk
            s._all_upblk_reads [ blk ] |= m._func_reads[u]
            s._all_upblk_writes[ blk ] |= m._func_writes[u]

            for v in m._func_calls[ u ]:
              if v in caller: # v calls someone else there is a cycle
                raise InvalidFuncCallError( \
                  "In class {}\nThe full call hierarchy:\n - {}{}\nThese function calls form a cycle:\n {}\n{}".format(
                    type(m).__name__, # function's hostobj must be m
                    "\n - ".join( [ "{} calls {}".format( caller[x][0].__name__, x.__name__ )
                                    for x in stk ] ),
                    "\n - {} calls {}".format( u.__name__, v.__name__ ),
                    "\n ".join( [ ">>> {} calls {}".format( caller[x][0].__name__, x.__name__)
                                    for x in stk[caller[v][1]+1: ] ] ),
                    " >>> {} calls {}".format( u.__name__, v.__name__ ) ) )

              caller[ v ] = ( u, len(stk) )
              stk.append( v )
              dfs( v, stk )
              del caller[ v ]
              stk.pop()

          # callee's id: (func, the caller's idx in stk)
          caller = { call: ( blk, 0 ) }
          stk    = [ call ] # for error message
          dfs( call, stk )

  def _uncollect_vars( s, m ):
    super( ComponentLevel2, s )._uncollect_vars( m )

    if isinstance( m, ComponentLevel2 ):
      s._all_update_on_edge -= m._update_on_edge

      for k in m._RD_U_constraints:
        s._all_RD_U_constraints[k] -= m._RD_U_constraints[k]
      for k in m._WR_U_constraints:
        s._all_WR_U_constraints[k] -= m._RD_U_constraints[k]

      for k in m._upblks:
        del s._all_upblk_reads[k]
        del s._all_upblk_writes[k]
        del s._all_upblk_calls[k]

  def _check_upblk_writes( s ):

    write_upblks = defaultdict(set)
    for blk, writes in s._all_upblk_writes.iteritems():
      for wr in writes:
        write_upblks[ wr ].add( blk )

    for obj, wr_blks in write_upblks.iteritems():
      wr_blks = list(wr_blks)

      if len(wr_blks) > 1:
        raise MultiWriterError( \
        "Multiple update blocks write {}.\n - {}".format( repr(obj),
            "\n - ".join([ x.__name__+" at "+repr(s._all_upblk_hostobj[x]) \
                           for x in wr_blks ]) ) )

      # See VarConstraintPass.py for full information
      # 1) WR A.b.b.b, A.b.b, A.b, A (detect 2-writer conflict)

      x = obj
      while x.is_signal():
        if x is not obj and x in write_upblks:
          wrx_blks = list(write_upblks[x])

          if wrx_blks[0] != wr_blks[0]:
            raise MultiWriterError( \
            "Two-writer conflict in nested struct/slice. \n - {} (in {})\n - {} (in {})".format(
              repr(x), wrx_blks[0].__name__,
              repr(obj), wr_blks[0].__name__ ) )
        x = x.get_parent_object()

      # 4) WR A.b[1:10], A.b[0:5], A.b[6] (detect 2-writer conflict)

      for x in obj.get_sibling_slices():
        # Recognize overlapped slices
        if x.slice_overlap( obj ) and x in write_upblks:
          wrx_blks = list(write_upblks[x])
          raise MultiWriterError( \
            "Two-writer conflict between sibling slices. \n - {} (in {})\n - {} (in {})".format(
              repr(x), wrx_blks[0].__name__,
              repr(obj), wr_blks[0].__name__ ) )

  def _check_port_in_upblk( s ):

    # Check read first
    for blk, reads in s._all_upblk_reads.iteritems():

      blk_hostobj = s._all_upblk_hostobj[ blk ]

      for obj in reads:
        host = obj
        while not isinstance( host, ComponentLevel2 ):
          host = host._parent_obj # go to the component

        if   isinstance( obj, InVPort ):  pass
        elif isinstance( obj, OutVPort ): pass
        elif isinstance( obj, Wire ):
          if blk_hostobj != host:
            raise SignalTypeError("""[Type 1] Invalid read to Wire:

- Wire "{}" of {} (class {}) is read in update block
       "{}" of {} (class {}).

  Note: Please only read Wire "x.wire" in x's update block.
        (Or did you intend to declare it as an OutVPort?)""" \
          .format(  repr(obj), repr(host), type(host).__name__,
                    blk.__name__, repr(blk_hostobj), type(blk_hostobj).__name__ ) )

    # Then check write
    for blk, writes in s._all_upblk_writes.iteritems():

      blk_hostobj = s._all_upblk_hostobj[ blk ]

      for obj in writes:
        host = obj
        while not isinstance( host, ComponentLevel2 ):
          host = host._parent_obj # go to the component

      # A continuous assignment is implied when a variable is connected to
      # an input port declaration. This makes assignments to a variable
      # declared as an input port illegal. -- IEEE

        if   isinstance( obj, InVPort ):
          if host._parent_obj != blk_hostobj:
            raise SignalTypeError("""[Type 2] Invalid write to an input port:

- InVPort "{}" of {} (class {}) is written in update block
          "{}" of {} (class {}).

  Note: Please only write to children's InVPort "x.y.in", not "x.in", in x's update block.""" \
          .format(  repr(obj), repr(host), type(host).__name__,
                    blk.__name__, repr(host), type(host).__name__ ) )

      # A continuous assignment is implied when a variable is connected to
      # the output port of an instance. This makes procedural or
      # continuous assignments to a variable connected to the output port
      # of an instance illegal. -- IEEE

        elif isinstance( obj, OutVPort ):
          if blk_hostobj != host:
            raise SignalTypeError("""[Type 3] Invalid write to output port:

- OutVPort \"{}\" of {} (class {}) is written in update block
           \"{}\" of {} (class {}).

  Note: Please only write to OutVPort "x.out", not "x.y.out", in x's update block.""" \
          .format(  repr(obj), repr(host), type(host).__name__,
                    blk.__name__, repr(blk_hostobj), type(blk_hostobj).__name__, ) )

      # The case of wire is special. We only allow Wire to be written in
      # the same object. One cannot write this from outside

        elif isinstance( obj, Wire ):
          if blk_hostobj != host:
            raise SignalTypeError("""[Type 4] Invalid write to Wire:

- Wire "{}" of {} (class {}) is written in update block
       "{}" of {} (class {}).

  Note: Please only write to Wire "x.wire" in x's update block.
        (Or did you intend to declare it as an InVPort?)""" \
          .format(  repr(obj), repr(host), type(host).__name__,
                    blk.__name__, repr(blk_hostobj), type(blk_hostobj).__name__ ) )

  #-----------------------------------------------------------------------
  # Construction-time APIs
  #-----------------------------------------------------------------------

  def func( s, func ): # @s.func is for those functions
    name = func.__name__
    if name in s._name_func or name in s._name_upblk:
      raise UpblkFuncSameNameError( name )

    s._name_func[ name ] = func
    s._cache_func_meta( func )
    return func

  # Override
  def update( s, blk ):
    super( ComponentLevel2, s ).update( blk )
    s._cache_func_meta( blk ) # add caching of src/ast
    return blk

  def update_on_edge( s, blk ):
    s._update_on_edge.add( blk )
    return s.update( blk )

  # Override
  def add_constraints( s, *args ): # add RD-U/WR-U constraints

    for (x0, x1) in args:
      if   isinstance( x0, U ) and isinstance( x1, U ): # U & U, same
        assert (x0.func, x1.func) not in s._U_U_constraints, \
          "Duplicated constraint"
        s._U_U_constraints.add( (x0.func, x1.func) )

      elif isinstance( x0, ValueConstraint ) and isinstance( x1, ValueConstraint ):
        raise InvalidConstraintError

      elif isinstance( x0, ValueConstraint ) or isinstance( x1, ValueConstraint ):
        sign = 1 # RD(x) < U is 1, RD(x) > U is -1
        if isinstance( x1, ValueConstraint ):
          sign = -1
          x0, x1 = x1, x0 # Make sure x0 is RD/WR(...) and x1 is U(...)

        if isinstance( x0, RD ):
          assert (sign, x1.func) not in s._RD_U_constraints[ x0.var ], \
            "Duplicated constraint"
          s._RD_U_constraints[ x0.var ].add( (sign, x1.func) )
        else:
          assert (sign, x1.func ) not in s._WR_U_constraints[ x0.var ], \
            "Duplicated constraint"
          s._WR_U_constraints[ x0.var ].add( (sign, x1.func) )

  #-----------------------------------------------------------------------
  # elaborate
  #-----------------------------------------------------------------------

  # Override
  def elaborate( s ):
    if s._constructed:
      return

    NamedObject.elaborate( s )
    s._declare_vars()

    s._all_components = s._collect( lambda x: isinstance( x, ComponentLevel2 ) )
    for c in s._all_components:
      c._elaborate_top = s
      c._elaborate_read_write_func()
      s._collect_vars( c )

    s._all_signals = s._collect( lambda x: isinstance( x, Signal ) )

    s.check()

  #-----------------------------------------------------------------------
  # Public APIs (only can be called after elaboration)
  #-----------------------------------------------------------------------

  def lock_in_simulation( s ):
    assert s._elaborate_top is s, "Locking in simulation " \
                                  "is only allowed at top, but this API call " \
                                  "is on {}.".format( "top."+repr(s)[2:] )
    s._swapped_signals = defaultdict(list)
    s._swapped_values  = defaultdict(list)

    def cleanup_connectables( current_obj, host_component ):

      # Deduplicate code. Choose operation based on type of current_obj
      if isinstance( current_obj, list ):
        iterable = enumerate( current_obj )
        is_list = True
      elif isinstance( current_obj, NamedObject ):
        iterable = current_obj.__dict__.iteritems()
        is_list = False
      else:
        return

      for i, obj in iterable:
        if not is_list and i.startswith("_"): # impossible to have tuple
          continue

        if   isinstance( obj, ComponentLevel1 ):
          cleanup_connectables( obj, obj )
        elif isinstance( obj, Interface ):
          cleanup_connectables( obj, host_component )
        elif isinstance( obj, list ):
          cleanup_connectables( obj, host_component )

        elif isinstance( obj, Signal ):
          try:
            if is_list: current_obj[i] = obj.default_value()
            else:       setattr( current_obj, i, obj.default_value() )
          except Exception as err:
            err.message = repr(obj) + " -- " + err.message
            err.args = (err.message,)
            raise err
          s._swapped_signals[ host_component ].append( (current_obj, i, obj, is_list) )

    cleanup_connectables( s, s )
    s._locked_simulation = True

  def unlock_simulation( s ):
    assert s._elaborate_top is s, "Unlocking simulation " \
                                  "is only allowed at top, but this API call " \
                                  "is on {}.".format( "top."+repr(s)[2:] )
    try:
      assert s._locked_simulation
    except:
      raise AttributeError("Cannot unlock an unlocked/never locked model.")

    for component, records in s._swapped_signals.iteritems():
      for current_obj, i, obj, is_list in records:
        if is_list:
          s._swapped_values[ component ] = ( current_obj, i, current_obj[i], is_list )
          current_obj[i] = obj
        else:
          s._swapped_values[ component ] = ( current_obj, i, getattr(current_obj, i), is_list )
          setattr( current_obj, i, obj )

    s._locked_simulation = False

  # TODO rename
  def check( s ):
    s._check_upblk_writes()
    s._check_port_in_upblk()

  def get_update_block_ast_pairs( s ):
    try:
      name_ast = s.__class__._name_ast
    except AttributeError: # This component doesn't have update block
      return set()

    return set([ (upblk, name_ast[name]) for name, upblk in s._name_upblk.iteritems() ] )

  def get_all_upblk_on_edge( s ):
    try:
      assert s._elaborate_top is s, "Getting all update_on_edge blocks  " \
                                    "is only allowed at top, but this API call " \
                                    "is on {}.".format( "top."+repr(s)[2:] )
      return s._all_update_on_edge
    except AttributeError:
      return NotElaboratedError()

  def get_all_upblk_metadata( s ):
    try:
      assert s._elaborate_top is s, "Getting all update block metadata  " \
                                    "is only allowed at top, but this API call " \
                                    "is on {}.".format( "top."+repr(s)[2:] )
      return s._all_upblk_reads, s._all_upblk_writes, s._all_upblk_calls
    except AttributeError:
      return NotElaboratedError()

  # Override
  def get_all_explicit_constraints( s ):
    try:
      assert s._elaborate_top is s, "Getting all explicit constraints " \
                                    "is only allowed at top, but this API call " \
                                    "is on {}.".format( "top."+repr(s)[2:] )
      return s._all_U_U_constraints, \
             s._RD_U_constraints, \
             s._WR_U_constraints
    except AttributeError:
      raise NotElaboratedError()

  def get_all_object_filter( s, filt ):
    assert callable( filt )
    try:
      return set( [ x for x in s._all_components | s._all_signals if filt(x) ] )
    except AttributeError:
      return s._collect( filt )

  # Override
  def delete_component_by_name( s, name ):

    # This nested delete function is to create an extra layer to properly
    # call garbage collector

    def _delete_component_by_name( parent, name ):
      obj = getattr( parent, name )
      top = s._elaborate_top

      # Remove all components and uncollect metadata

      removed_components = obj.get_all_components()
      top._all_components -= removed_components

      for x in removed_components:
        assert x._elaborate_top is top
        top._uncollect_vars( x )

      for x in obj._collect():
        del x._parent_obj

      top._all_signals -= obj._collect( lambda x: isinstance( x, Signal ) )

      delattr( s, name )

    _delete_component_by_name( s, name )
    import gc
    gc.collect()

  # Override
  def add_component_by_name( s, name, obj ):
    assert not hasattr( s, name )
    NamedObject.__setattr__ = NamedObject.__setattr_for_elaborate__
    setattr( s, name, obj )
    del NamedObject.__setattr__

    top = s._elaborate_top

    added_components = obj.get_all_components()
    top._all_components |= added_components

    for c in added_components:
      c._elaborate_top = top
      c._elaborate_read_write_func()
      top._collect_vars( c )

    added_signals = obj._collect( lambda x: isinstance( x, Signal ) )
    top._all_signals |= added_signals
