{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "SUM",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "FLOAT"
        }
      ],
      "type": "FLOAT",
      "name": "EXPR$0"
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "FLOAT"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 6,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "GREATER_THAN_OR_EQUAL",
                "operator": ">=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 1,
                    "name": "$1",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "201308",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 6
                  }
                ]
              },
              {
                "kind": "LESS_THAN_OR_EQUAL",
                "operator": "<=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 1,
                    "name": "$1",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "201311",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 6
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "yearmonth",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}