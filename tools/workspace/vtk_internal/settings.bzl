# This file contains configuration settings for how Drake should configure its
# private build of VTK.
#
# Each key in the MODULE_SETTINGS dict provides the settings for the VTK module
# of that name. If a module does not need any settings beyond the defaults, it
# can be omitted from the dict.
#
# The kinds of settings available for a module are documented in rules.bzl on
# the vtk_cc_module() rule.

MODULE_SETTINGS = {
    # First, we'll configure VTK's first-party utility libraries.
    "ABI": {
        "cmake_defines": [
            "VTK_ABI_NAMESPACE_BEGIN=inline namespace drake_vendor __attribute__ ((visibility (\"hidden\"))) {",  # noqa
            "VTK_ABI_NAMESPACE_END=}",
        ],
    },
    "VTK::kwiml": {
        "cmake_defines": [
            # Match the Utilities/KWIML/vtkkwiml/CMakeLists.txt value. (This
            # version number hasn't changed in 8+ years, so we won't bother
            # automating it.)
            "KWIML_VERSION=1.0.0",
            "KWIML_VERSION_DECIMAL=1000000",
        ],
    },
    "VTK::vtksys": {
        # Upstream, this module has opt-in flags for each small feature within
        # the module. The VTK CMake script chooses which features to enable. To
        # match that logic here, we'll opt-out the default srcs glob and opt-in
        # to specific files that match how upstream VTK configures KWSys (with
        # exceptions noted inline below).
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "Utilities/KWSys/vtksys/Base64.c",
            # CommandLineArguments.cxx is unused in Drake, so is omitted here.
            "Utilities/KWSys/vtksys/Directory.cxx",
            "Utilities/KWSys/vtksys/DynamicLoader.cxx",
            "Utilities/KWSys/vtksys/EncodingC.c",
            "Utilities/KWSys/vtksys/EncodingCXX.cxx",
            "Utilities/KWSys/vtksys/FStream.cxx",
            # Glob.cxx is unused in Drake, so is omitted here.
            "Utilities/KWSys/vtksys/MD5.c",
            "Utilities/KWSys/vtksys/ProcessUNIX.c",
            "Utilities/KWSys/vtksys/RegularExpression.cxx",
            "Utilities/KWSys/vtksys/String.c",
            "Utilities/KWSys/vtksys/Status.cxx",
            "Utilities/KWSys/vtksys/System.c",
            "Utilities/KWSys/vtksys/SystemInformation.cxx",
            "Utilities/KWSys/vtksys/SystemTools.cxx",
        ],
        "cmake_defines": [
            # Match the VTK defaults.
            "KWSYS_NAMESPACE=vtksys",
            "KWSYS_NAME_IS_KWSYS=0",
            "KWSYS_SYSTEMTOOLS_USE_TRANSLATION_MAP=1",
            # Features that are available on the host platform.
            "KWSYS_STL_HAS_WSTRING=1",
            # Features that are NOT available on the host platform.
            "KWSYS_CXX_HAS_EXT_STDIO_FILEBUF_H=0",
            # The *module* prefix and suffix are the same on Linux and macOS.
            # https://gitlab.kitware.com/cmake/cmake/-/issues/21189
            "KWSYS_DynamicLoader_PREFIX=lib",
            "KWSYS_DynamicLoader_SUFFIX=.so",
            # Library API choices.
            "KWSYS_BUILD_SHARED=0",
        ],
        "copts_extra": [
            # Match the VTK defaults.
            "-DKWSYS_NAMESPACE=vtksys",
            # Features that are available on the host platform.
            "-DKWSYS_SYS_HAS_IFADDRS_H",
            "-DKWSYS_CXX_HAS_SETENV",
            "-DKWSYS_CXX_HAS_UNSETENV",
            "-DKWSYS_CXX_HAS_UTIMENSAT",
        ] + select({
            ":osx": [
                "-DKWSYS_CXX_STAT_HAS_ST_MTIMESPEC",
            ],
            "//conditions:default": [
                "-DKWSYS_CXX_STAT_HAS_ST_MTIM",
            ],
        }),
    },

    # Second, we'll configure the modules Drake needs (in alphabetical order).
    "VTK::CommonCore": {
        "visibility": ["//visibility:public"],
        "hdrs_glob_exclude": [
            # These header files are consumed by bespoke configure_file logic,
            # so we don't want to automatically configure them. Instead, we'll
            # use generate_common_core_sources() from rules.bzl to handle it.
            "Common/Core/vtkArrayDispatchArrayList.h.in",
            "Common/Core/vtkTypeListMacros.h.in",
            "Common/Core/vtkTypedArray.h.in",
        ],
        "hdrs_extra": [
            # These are the hdrs outputs of generate_common_core_sources() from
            # rules.bzl (related to the hdrs_glob_exclude, immediately above).
            ":common_core_array_dispatch_array_list",
            ":common_core_type_list_macros",
            ":common_core_vtk_type_arrays_hdrs",
        ],
        "included_cxxs": [
            "Common/Core/vtkMersenneTwister_Private.cxx",
            "Common/Core/vtkVariantToNumeric.cxx",
        ],
        "srcs_extra": [
            # Sources in nested subdirs are not globbed by default, so we need
            # to list the nested sources we want explicitly.
            "Common/Core/SMP/Common/vtkSMPToolsAPI.cxx",
            "Common/Core/SMP/Sequential/vtkSMPToolsImpl.cxx",
            # These header files are generated by custom configure_file logic.
            # See generate_common_core_sources() in rules.bzl.
            ":common_core_array_instantiations",
            ":common_core_vtk_type_arrays_srcs",
        ],
        "srcs_glob_exclude": [
            # Optional files that we choose not to enable.
            "Common/Core/vtkAndroid*",
            "Common/Core/vtkWin32*",
        ],
        "cmake_defines_cmakelists": [
            # Scrape the VTK_..._VERSION definitions from this file.
            "CMake/vtkVersion.cmake",
        ],
        "cmake_defines": [
            # Features that are available on the host platform.
            "VTK_HAS_ISFINITE=1",
            "VTK_HAS_ISINF=1",
            "VTK_HAS_ISNAN=1",
            "VTK_HAS_STD_ISFINITE=1",
            "VTK_HAS_STD_ISINF=1",
            "VTK_HAS_STD_ISNAN=1",
            "VTK_USE_64BIT_IDS=1",
            "VTK_USE_64BIT_TIMESTAMPS=1",
            # Threading.
            "VTK_MAX_THREADS=1",
            "VTK_SMP_DEFAULT_IMPLEMENTATION_SEQUENTIAL=1",
            "VTK_SMP_ENABLE_SEQUENTIAL=1",
            "VTK_SMP_IMPLEMENTATION_TYPE=Sequential",
            "VTK_USE_PTHREADS=1",
            # Library API choices.
            "VTK_ALL_NEW_OBJECT_FACTORY=1",
            "VTK_ALWAYS_OPTIMIZE_ARRAY_ITERATORS=1",
            "VTK_LEGACY_REMOVE=1",
            "VTK_USE_FUTURE_CONST=1",
            "VTK_WARN_ON_DISPATCH_FAILURE=1",
        ],
        "cmake_undefines": [
            # Features that are NOT available on the host platform.
            "VTK_HAS_FEENABLEEXCEPT",
            "VTK_HAS_FINITE",
            "VTK_HAS__FINITE",
            "VTK_HAS__ISNAN",
            "VTK_REQUIRE_LARGE_FILE_SUPPORT",
            # All of Drake's supported CPUs are little-endian. If we ever do
            # need to support, this we can patch the header file to always
            # use the built-in __BIG_ENDIAN__ (instead of only on macOS).
            "VTK_WORDS_BIGENDIAN",
            # Threading.
            "VTK_SMP_DEFAULT_IMPLEMENTATION_OPENMP",
            "VTK_SMP_DEFAULT_IMPLEMENTATION_STDTHREAD",
            "VTK_SMP_DEFAULT_IMPLEMENTATION_TBB",
            "VTK_SMP_ENABLE_OPENMP",
            "VTK_SMP_ENABLE_STDTHREAD",
            "VTK_SMP_ENABLE_TBB",
            "VTK_USE_WIN32_THREADS",
            # Library API choices.
            "VTK_BUILD_SHARED_LIBS",
            "VTK_DEBUG_LEAKS",
            "VTK_DEBUG_RANGE_ITERATORS",
            "VTK_LEGACY_SILENT",
            "VTK_USE_MEMKIND",
            "VTK_USE_SCALED_SOA_ARRAYS",
        ],
        # Allow a circular dependency between CommonCore <=> CommonDataModel.
        # See also patches/common_core_vs_data_model_cycle.patch.
        "copts_extra": [
            "-DvtkCommonDataModel_ENABLED",
        ],
        "deps_extra": [
            ":VTK__CommonDataModel_vtkDataObject",
        ],
    },
    "VTK::CommonDataModel": {
        "visibility": ["//visibility:public"],
    },
    "VTK::CommonExecutionModel": {
        "visibility": ["//visibility:public"],
    },
    "VTK::CommonMath": {
        # This module has a lot of code we don't need. We'll opt-out of the
        # default srcs glob, and instead just specify what Drake needs.
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "Common/Math/vtkMatrix3x3.cxx",
            "Common/Math/vtkMatrix4x4.cxx",
        ],
        "module_deps_ignore": [
            "VTK::kissfft",
        ],
    },
    "VTK::CommonMisc": {
        # This module has a lot of code we don't need. We'll opt-out of the
        # default srcs glob, and instead just specify what Drake needs.
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "Common/Misc/vtkErrorCode.cxx",
            "Common/Misc/vtkHeap.cxx",
        ],
        "module_deps_ignore": [
            "VTK::exprtk",
        ],
    },
    "VTK::CommonSystem": {
        # This module has a lot of code we don't need. We'll opt-out of the
        # default srcs glob, and instead just specify what Drake needs.
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "Common/System/vtkDirectory.cxx",
            "Common/System/vtkTimerLog.cxx",
        ],
    },
    "VTK::CommonTransforms": {
        # This isn't used directly by Drake, but is used by other VTK modules.
    },
    "VTK::FiltersCore": {
        "visibility": ["//visibility:public"],
        # This module has a lot of code we don't need. We'll opt-out of the
        # default srcs glob, and instead just specify what Drake needs.
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "Filters/Core/vtkAppendPolyData.cxx",
            "Filters/Core/vtkDecimatePro.cxx",
            "Filters/Core/vtkGlyph3D.cxx",
            "Filters/Core/vtkPolyDataNormals.cxx",
            "Filters/Core/vtkPolyDataTangents.cxx",
            "Filters/Core/vtkTriangleFilter.cxx",
        ],
    },
    "VTK::IOCore": {
        "srcs_glob_exclude": [
            # Skip code we don't need.
            "**/*Codec*",
            "**/*Glob*",
            "**/*Particle*",
            "**/*Java*",
            "**/*UTF*",
            # Skip this to avoid a dependency on lz4.
            "**/*LZ4*",
            # Skip this to avoid a dependency on lzma.
            "**/*LZMA*",
        ],
        "module_deps_ignore": [
            "VTK::lz4",
            "VTK::lzma",
        ],
    },
    "VTK::IOGeometry": {
        "visibility": ["//visibility:public"],
        # This module has a lot of code we don't need. We'll opt-out of the
        # default srcs glob, and instead just specify what Drake needs.
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "IO/Geometry/vtkOBJWriter.cxx",
            "IO/Geometry/vtkSTLReader.cxx",
        ],
        "module_deps_ignore": [
            "VTK::IOLegacy",
            "VTK::FiltersGeneral",
            "VTK::FiltersHybrid",
            "VTK::RenderingCore",
            "VTK::nlohmannjson",
        ],
    },
    "VTK::IOImage": {
        "visibility": ["//visibility:public"],
        # This module has a lot of code we don't need. We'll opt-out of the
        # default srcs glob, and instead just specify what Drake needs.
        "srcs_glob_exclude": ["**"],
        "srcs_extra": [
            "IO/Image/vtkImageExport.cxx",
            "IO/Image/vtkImageReader2.cxx",
            "IO/Image/vtkImageReader2Collection.cxx",
            "IO/Image/vtkImageReader2Factory.cxx",
            "IO/Image/vtkImageWriter.cxx",
            "IO/Image/vtkJPEGReader.cxx",
            "IO/Image/vtkJPEGWriter.cxx",
            "IO/Image/vtkPNGReader.cxx",
            "IO/Image/vtkPNGWriter.cxx",
            "IO/Image/vtkTIFFReader.cxx",
            "IO/Image/vtkTIFFWriter.cxx",
        ],
        "module_deps_ignore": [
            "VTK::DICOMParser",
            "VTK::metaio",
        ],
    },
    "VTK::ImagingCore": {
        "visibility": ["//visibility:public"],
    },

    # Third, we'll configure dependencies that come from Drake's WORKSPACE.
    "VTK::jpeg": {
        "cmake_defines": [
            "VTK_MODULE_USE_EXTERNAL_vtkjpeg=1",
        ],
        "hdrs_glob_exclude": [
            "ThirdParty/jpeg/vtkjpeg/**",
        ],
        "deps_extra": [
            # TODO(jwnimmer-tri) VTK is the only user of this library.
            # We should write our own WORKSPACE rule to build it sensibly,
            # or switch to VTK's vendored version.
            "@libjpeg",
        ],
    },
    "VTK::png": {
        "cmake_defines": [
            "VTK_MODULE_USE_EXTERNAL_vtkpng=1",
        ],
        "deps_extra": [
            # TODO(jwnimmer-tri) VTK is the only user of this library.
            # We should write our own WORKSPACE rule to build it sensibly,
            # or switch to VTK's vendored version.
            "@libpng",
        ],
    },
    "VTK::tiff": {
        "cmake_defines": [
            "VTK_MODULE_USE_EXTERNAL_vtktiff=1",
        ],
        "deps_extra": [
            # TODO(jwnimmer-tri) VTK is the only user of this library.
            # We should write our own WORKSPACE rule to build it sensibly,
            # or switch to VTK's vendored version.
            "@libtiff",
        ],
    },
    "VTK::zlib": {
        "cmake_defines": [
            "VTK_MODULE_USE_EXTERNAL_vtkzlib=1",
        ],
        "deps_extra": [
            "@zlib",
        ],
    },

    # Fourth, we'll configure dependencies that we let VTK build and vendor on
    # its own, because nothing else in Drake needs these.
    "VTK::doubleconversion": {
        "cmake_undefines": [
            "VTK_MODULE_USE_EXTERNAL_vtkdoubleconversion",
        ],
        "hdrs_content": {
            "ThirdParty/doubleconversion/vtkdoubleconversion_export.h": """
                #pragma once
                #define VTKDOUBLECONVERSION_EXPORT
            """,
        },
        "srcs_glob_extra": [
            "ThirdParty/doubleconversion/**/*.cc",
        ],
    },
    "VTK::pugixml": {
        # TODO(jwnimmer-tri) The only user of pugixml is vtkDataAssembly.
        # Possibly there is some way to disable XML I/O support on that
        # class, so that we can drop this dependency.
        "cmake_undefines": [
            "VTK_MODULE_USE_EXTERNAL_vtkpugixml",
        ],
        "hdrs_glob_exclude": [
            # We use `hdrs_content` instead of a full-blown configure file.
            "**/pugiconfig.hpp.in",
        ],
        "hdrs_content": {
            "ThirdParty/pugixml/pugiconfig.hpp": """
                #pragma once
                #define PUGIXML_API
            """,
        },
        "srcs_glob_extra": [
            "ThirdParty/pugixml/**/*.cpp",
        ],
    },
    "VTK::utf8": {
        "cmake_undefines": [
            "VTK_MODULE_USE_EXTERNAL_vtkutf8",
        ],
    },
}
