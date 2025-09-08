{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "MINUS",
      "operator": "-",
      "type": "FLOAT",
      "operands": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "FLOAT"
        },
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "FLOAT"
        }
      ]
    }
  ],
  "id": "4",
  "inputs": [
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
          "name": null
        },
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "FLOAT",
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
                      "value": 7,
                      "type": "INTEGER",
                      "nullable": false,
                      "precision": 10
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "FLOAT",
                  "nullable": false,
                  "precision": 15
                }
              ]
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "FLOAT",
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
                      "value": 5,
                      "type": "INTEGER",
                      "nullable": false,
                      "precision": 10
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "FLOAT",
                  "nullable": false,
                  "precision": 15
                }
              ]
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
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
                    "value": "201304",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
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
  ]
}