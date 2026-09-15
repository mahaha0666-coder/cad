# CMake generated Testfile for 
# Source directory: /home/lysq/CAD/libredwg/libredwg
# Build directory: /home/lysq/CAD/libredwg-build
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test([=[alive.test]=] "alive.test")
set_tests_properties([=[alive.test]=] PROPERTIES  _BACKTRACE_TRIPLES "/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;374;add_test;/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;0;")
add_test([=[dxf.test]=] "dxf.test")
set_tests_properties([=[dxf.test]=] PROPERTIES  _BACKTRACE_TRIPLES "/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;377;add_test;/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;0;")
add_test([=[json.test]=] "json.test")
set_tests_properties([=[json.test]=] PROPERTIES  _BACKTRACE_TRIPLES "/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;380;add_test;/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;0;")
add_test([=[dwgfilter.test]=] "dwgfilter.test")
set_tests_properties([=[dwgfilter.test]=] PROPERTIES  _BACKTRACE_TRIPLES "/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;382;add_test;/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;0;")
add_test([=[svg.test]=] "svg.test")
set_tests_properties([=[svg.test]=] PROPERTIES  _BACKTRACE_TRIPLES "/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;386;add_test;/home/lysq/CAD/libredwg/libredwg/CMakeLists.txt;0;")
subdirs("test/unit-testing")
