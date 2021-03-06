# Copyright 2017 Google Inc.  All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for variant_to_bigquery module."""

from __future__ import absolute_import

import unittest

from apache_beam.io.gcp.internal.clients import bigquery
from apache_beam import ParDo
from apache_beam.testing.test_pipeline import TestPipeline
from apache_beam.testing.util import assert_that
from apache_beam.testing.util import equal_to
from apache_beam.transforms import Create

from gcp_variant_transforms.beam_io import vcfio
from gcp_variant_transforms.beam_io import vcf_header_io
from gcp_variant_transforms.libs import bigquery_schema_descriptor
from gcp_variant_transforms.libs import bigquery_row_generator
from gcp_variant_transforms.libs import processed_variant
from gcp_variant_transforms.libs import vcf_field_conflict_resolver
from gcp_variant_transforms.libs.bigquery_util import ColumnKeyConstants
from gcp_variant_transforms.libs.bigquery_util import TableFieldConstants
from gcp_variant_transforms.transforms import variant_to_bigquery
from gcp_variant_transforms.transforms.variant_to_bigquery import _ConvertToBigQueryTableRow as ConvertToBigQueryTableRow


class ConvertToBigQueryTableRowTest(unittest.TestCase):
  """Test cases for the ``ConvertToBigQueryTableRow`` DoFn."""

  def setUp(self):
    self._schema_descriptor = bigquery_schema_descriptor.SchemaDescriptor(
        self._get_table_schema())
    self._conflict_resolver = (
        vcf_field_conflict_resolver.FieldConflictResolver())

    self._row_generator = bigquery_row_generator.BigQueryRowGenerator(
        self._schema_descriptor, self._conflict_resolver)

  def _get_table_schema(self):
    # type (None) -> bigquery.TableSchema
    schema = bigquery.TableSchema()
    schema.fields.append(bigquery.TableFieldSchema(
        name='II',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='INFO foo desc'))
    schema.fields.append(bigquery.TableFieldSchema(
        name='IFR',
        type=TableFieldConstants.TYPE_FLOAT,
        mode=TableFieldConstants.MODE_REPEATED,
        description='INFO foo desc'))
    schema.fields.append(bigquery.TableFieldSchema(
        name='IFR2',
        type=TableFieldConstants.TYPE_FLOAT,
        mode=TableFieldConstants.MODE_REPEATED,
        description='INFO foo desc'))
    schema.fields.append(bigquery.TableFieldSchema(
        name='IS',
        type=TableFieldConstants.TYPE_STRING,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='INFO foo desc'))
    schema.fields.append(bigquery.TableFieldSchema(
        name='ISR',
        type=TableFieldConstants.TYPE_STRING,
        mode=TableFieldConstants.MODE_REPEATED,
        description='INFO foo desc'))
    # Call record.
    call_record = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_BOOLEAN,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))
    call_record.fields.append(bigquery.TableFieldSchema(
        name='GQ',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))
    call_record.fields.append(bigquery.TableFieldSchema(
        name='FIR',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    schema.fields.append(call_record)
    return schema

  def _get_sample_variant_1(self, split_alternate_allele_info_fields=True):
    variant = vcfio.Variant(
        reference_name='chr19', start=11, end=12, reference_bases='C',
        alternate_bases=['A', 'TT'], names=['rs1', 'rs2'], quality=2,
        filters=['PASS'],
        info={'IFR': vcfio.VariantInfo([0.1, 0.2], 'A'),
              'IFR2': vcfio.VariantInfo([0.2, 0.3], 'A'),
              'IS': vcfio.VariantInfo('some data', '1'),
              'ISR': vcfio.VariantInfo(['data1', 'data2'], '2')},
        calls=[
            vcfio.VariantCall(
                name='Sample1', genotype=[0, 1], phaseset='*',
                info={'GQ': 20, 'FIR': [10, 20]}),
            vcfio.VariantCall(
                name='Sample2', genotype=[1, 0],
                info={'GQ': 10, 'FB': True}),
        ]
    )
    row = {ColumnKeyConstants.REFERENCE_NAME: 'chr19',
           ColumnKeyConstants.START_POSITION: 11,
           ColumnKeyConstants.END_POSITION: 12,
           ColumnKeyConstants.REFERENCE_BASES: 'C',
           ColumnKeyConstants.NAMES: ['rs1', 'rs2'],
           ColumnKeyConstants.QUALITY: 2,
           ColumnKeyConstants.FILTER: ['PASS'],
           ColumnKeyConstants.CALLS: [
               {ColumnKeyConstants.CALLS_NAME: 'Sample1',
                ColumnKeyConstants.CALLS_GENOTYPE: [0, 1],
                ColumnKeyConstants.CALLS_PHASESET: '*',
                'GQ': 20, 'FIR': [10, 20]},
               {ColumnKeyConstants.CALLS_NAME: 'Sample2',
                ColumnKeyConstants.CALLS_GENOTYPE: [1, 0],
                ColumnKeyConstants.CALLS_PHASESET: None,
                'GQ': 10, 'FB': True}],
           'IS': 'some data',
           'ISR': ['data1', 'data2']}
    if split_alternate_allele_info_fields:
      row[ColumnKeyConstants.ALTERNATE_BASES] = [
          {ColumnKeyConstants.ALTERNATE_BASES_ALT:
           'A', 'IFR': 0.1, 'IFR2': 0.2},
          {ColumnKeyConstants.ALTERNATE_BASES_ALT:
           'TT', 'IFR': 0.2, 'IFR2': 0.3}]
    else:
      row[ColumnKeyConstants.ALTERNATE_BASES] = [
          {ColumnKeyConstants.ALTERNATE_BASES_ALT: 'A'},
          {ColumnKeyConstants.ALTERNATE_BASES_ALT: 'TT'}]
      row['IFR'] = [0.1, 0.2]
      row['IFR2'] = [0.2, 0.3]
    return variant, row

  def _get_sample_variant_2(self):
    variant = vcfio.Variant(
        reference_name='20', start=123, end=125, reference_bases='CT',
        alternate_bases=[], filters=['q10', 's10'],
        info={'II': vcfio.VariantInfo(1234, '1')})
    row = {ColumnKeyConstants.REFERENCE_NAME: '20',
           ColumnKeyConstants.START_POSITION: 123,
           ColumnKeyConstants.END_POSITION: 125,
           ColumnKeyConstants.REFERENCE_BASES: 'CT',
           ColumnKeyConstants.ALTERNATE_BASES: [],
           ColumnKeyConstants.FILTER: ['q10', 's10'],
           ColumnKeyConstants.CALLS: [],
           'II': 1234}
    return variant, row

  def _get_sample_variant_3(self):
    variant = vcfio.Variant(
        reference_name='20', start=None, end=None, reference_bases=None)
    row = {ColumnKeyConstants.REFERENCE_NAME: '20',
           ColumnKeyConstants.START_POSITION: None,
           ColumnKeyConstants.END_POSITION: None,
           ColumnKeyConstants.REFERENCE_BASES: None,
           ColumnKeyConstants.ALTERNATE_BASES: [],
           ColumnKeyConstants.CALLS: []}
    return variant, row

  def _get_sample_variant_with_empty_calls(self):
    variant = vcfio.Variant(
        reference_name='20', start=123, end=125, reference_bases='CT',
        alternate_bases=[], filters=['q10', 's10'],
        info={'II': vcfio.VariantInfo(1234, '1')},
        calls=[
            vcfio.VariantCall(
                name='EmptySample', genotype=[], phaseset='*',
                info={}),
        ])
    row = {ColumnKeyConstants.REFERENCE_NAME: '20',
           ColumnKeyConstants.START_POSITION: 123,
           ColumnKeyConstants.END_POSITION: 125,
           ColumnKeyConstants.REFERENCE_BASES: 'CT',
           ColumnKeyConstants.ALTERNATE_BASES: [],
           ColumnKeyConstants.FILTER: ['q10', 's10'],
           ColumnKeyConstants.CALLS: [],
           'II': 1234}
    return variant, row

  def _get_sample_variant_with_incompatible_records(self):
    variant = vcfio.Variant(
        reference_name='chr19', start=11, end=12, reference_bases='C',
        alternate_bases=[], filters=['PASS'],
        info={'IFR': vcfio.VariantInfo(['0.1', '0.2'], '2'),
              'IS': vcfio.VariantInfo(1, '1'),
              'ISR': vcfio.VariantInfo(1, '1')},
        calls=[
            vcfio.VariantCall(
                name='Sample1', genotype=[0, 1], phaseset='*',
                info={'GQ': 20, 'FIR': [10.0, 20.0]}),
        ]
    )
    row = {ColumnKeyConstants.REFERENCE_NAME: 'chr19',
           ColumnKeyConstants.START_POSITION: 11,
           ColumnKeyConstants.END_POSITION: 12,
           ColumnKeyConstants.REFERENCE_BASES: 'C',
           ColumnKeyConstants.ALTERNATE_BASES: [],
           ColumnKeyConstants.FILTER: ['PASS'],
           ColumnKeyConstants.CALLS: [
               {ColumnKeyConstants.CALLS_NAME: 'Sample1',
                ColumnKeyConstants.CALLS_GENOTYPE: [0, 1],
                ColumnKeyConstants.CALLS_PHASESET: '*',
                'GQ': 20, 'FIR': [10, 20]}],
           'IFR': [0.1, 0.2],
           'IS': '1',
           'ISR': ['1']}
    return variant, row

  def test_convert_variant_to_bigquery_row(self):
    variant_1, row_1 = self._get_sample_variant_1()
    variant_2, row_2 = self._get_sample_variant_2()
    variant_3, row_3 = self._get_sample_variant_3()
    header_fields = vcf_header_io.VcfHeader()
    proc_var_1 = processed_variant.ProcessedVariantFactory(
        header_fields).create_processed_variant(variant_1)
    proc_var_2 = processed_variant.ProcessedVariantFactory(
        header_fields).create_processed_variant(variant_2)
    proc_var_3 = processed_variant.ProcessedVariantFactory(
        header_fields).create_processed_variant(variant_3)
    pipeline = TestPipeline(blocking=True)
    bigquery_rows = (
        pipeline
        | Create([proc_var_1, proc_var_2, proc_var_3])
        | 'ConvertToRow' >> ParDo(ConvertToBigQueryTableRow(
            self._row_generator)))
    assert_that(bigquery_rows, equal_to([row_1, row_2, row_3]))
    pipeline.run()

  def test_convert_variant_to_bigquery_row_omit_empty_calls(self):
    variant, row = self._get_sample_variant_with_empty_calls()
    header_fields = vcf_header_io.VcfHeader()
    proc_var = processed_variant.ProcessedVariantFactory(
        header_fields).create_processed_variant(variant)
    pipeline = TestPipeline(blocking=True)
    bigquery_rows = (
        pipeline
        | Create([proc_var])
        | 'ConvertToRow' >> ParDo(ConvertToBigQueryTableRow(
            self._row_generator, omit_empty_sample_calls=True)))
    assert_that(bigquery_rows, equal_to([row]))
    pipeline.run()

  def test_convert_variant_to_bigquery_row_allow_incompatible_recoreds(self):
    variant, row = self._get_sample_variant_with_incompatible_records()
    header_fields = vcf_header_io.VcfHeader()
    proc_var = processed_variant.ProcessedVariantFactory(
        header_fields).create_processed_variant(variant)
    pipeline = TestPipeline(blocking=True)
    bigquery_rows = (
        pipeline
        | Create([proc_var])
        | 'ConvertToRow' >> ParDo(ConvertToBigQueryTableRow(
            self._row_generator, allow_incompatible_records=True)))
    assert_that(bigquery_rows, equal_to([row]))
    pipeline.run()

  def test_merge_field_schemas_no_same_id(self):
    field_schemas_1 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='IFR',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_REPEATED,
            description='INFO foo desc')
    ]
    field_schemas_2 = [
        bigquery.TableFieldSchema(
            name='AB',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    merged_field_schemas = variant_to_bigquery._get_merged_field_schemas(
        field_schemas_1, field_schemas_2)
    expected_merged_field_schemas = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='IFR',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_REPEATED,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='AB',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    self.assertEqual(merged_field_schemas, expected_merged_field_schemas)

  def test_merge_field_schemas_same_id_no_conflicts(self):
    field_schemas_1 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='IFR',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_REPEATED,
            description='INFO foo desc')
    ]
    field_schemas_2 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='AB',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    merged_field_schemas = variant_to_bigquery._get_merged_field_schemas(
        field_schemas_1, field_schemas_2)
    expected_merged_field_schemas = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='IFR',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_REPEATED,
            description='INFO foo desc'),
        bigquery.TableFieldSchema(
            name='AB',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    self.assertEqual(merged_field_schemas, expected_merged_field_schemas)

  def test_merge_field_schemas_conflict_mode(self):
    field_schemas_1 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    field_schemas_2 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_REPEATED,
            description='INFO foo desc')
    ]
    self.assertRaises(ValueError, variant_to_bigquery._get_merged_field_schemas,
                      field_schemas_1, field_schemas_2)

  def test_merge_field_schemas_conflict_type(self):
    field_schemas_1 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_INTEGER,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    field_schemas_2 = [
        bigquery.TableFieldSchema(
            name='II',
            type=TableFieldConstants.TYPE_FLOAT,
            mode=TableFieldConstants.MODE_NULLABLE,
            description='INFO foo desc')
    ]
    self.assertRaises(ValueError, variant_to_bigquery._get_merged_field_schemas,
                      field_schemas_1, field_schemas_2)

  def test_merge_field_schemas_conflict_record_fields(self):
    call_record_1 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record_1.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_BOOLEAN,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))
    field_schemas_1 = [call_record_1]

    call_record_2 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record_2.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))
    field_schemas_2 = [call_record_2]
    self.assertRaises(ValueError, variant_to_bigquery._get_merged_field_schemas,
                      field_schemas_1, field_schemas_2)

  def test_merge_field_schemas_same_record(self):
    call_record_1 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record_1.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_BOOLEAN,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))

    field_schemas_1 = [call_record_1]
    field_schemas_2 = [call_record_1]

    expected_merged_field_schemas = [call_record_1]
    self.assertEqual(
        variant_to_bigquery._get_merged_field_schemas(field_schemas_1,
                                                      field_schemas_2),
        expected_merged_field_schemas)

  def test_merge_field_schemas_merge_record_fields(self):
    call_record_1 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record_1.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_BOOLEAN,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))

    field_schemas_1 = [call_record_1]

    call_record_2 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record_2.fields.append(bigquery.TableFieldSchema(
        name='GQ',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))
    field_schemas_2 = [call_record_2]

    call_record_3 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    call_record_3.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_BOOLEAN,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))
    call_record_3.fields.append(bigquery.TableFieldSchema(
        name='GQ',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_NULLABLE,
        description='FORMAT foo desc'))

    expected_merged_field_schemas = [call_record_3]
    self.assertEqual(
        variant_to_bigquery._get_merged_field_schemas(field_schemas_1,
                                                      field_schemas_2),
        expected_merged_field_schemas)

  def test_merge_field_schemas_conflict_inner_record_fields(self):
    record_1 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_1 = bigquery.TableFieldSchema(
        name='inner record',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_1.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    record_1.fields.append(inner_record_1)
    field_schemas_1 = [record_1]

    record_2 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_2 = bigquery.TableFieldSchema(
        name='inner record',
        type=TableFieldConstants.TYPE_INTEGER,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_2.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    record_2.fields.append(inner_record_2)
    field_schemas_2 = [record_2]
    self.assertRaises(ValueError, variant_to_bigquery._get_merged_field_schemas,
                      field_schemas_1, field_schemas_2)

  def test_merge_field_schemas_merge_inner_record_fields(self):
    record_1 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_1 = bigquery.TableFieldSchema(
        name='inner record',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_1.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    record_1.fields.append(inner_record_1)
    field_schemas_1 = [record_1]

    record_2 = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_2 = bigquery.TableFieldSchema(
        name='inner record',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    inner_record_2.fields.append(bigquery.TableFieldSchema(
        name='AB',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    record_2.fields.append(inner_record_2)
    field_schemas_2 = [record_2]

    merged_record = bigquery.TableFieldSchema(
        name=ColumnKeyConstants.CALLS,
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    merged_inner_record = bigquery.TableFieldSchema(
        name='inner record',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='One record for each call.')
    merged_inner_record.fields.append(bigquery.TableFieldSchema(
        name='FB',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    merged_inner_record.fields.append(bigquery.TableFieldSchema(
        name='AB',
        type=TableFieldConstants.TYPE_RECORD,
        mode=TableFieldConstants.MODE_REPEATED,
        description='FORMAT foo desc'))
    merged_record.fields.append(merged_inner_record)
    expected_merged_field_schemas = [merged_record]
    self.assertEqual(
        variant_to_bigquery._get_merged_field_schemas(field_schemas_1,
                                                      field_schemas_2),
        expected_merged_field_schemas)
