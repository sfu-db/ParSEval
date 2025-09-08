{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "INTEGER"
        }
      ],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "INTEGER",
          "operands": [
            {
              "kind": "LIKE",
              "operator": "LIKE",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "VARCHAR"
                },
                {
                  "kind": "LITERAL",
                  "value": "%arena,mtgo%",
                  "type": "CHAR",
                  "nullable": false,
                  "precision": 12
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": 1,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "INTEGER",
              "nullable": true,
              "precision": 10
            }
          ]
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "cards",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}